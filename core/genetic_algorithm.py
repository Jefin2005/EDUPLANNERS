"""
Genetic Algorithm Engine for EDUPLANNER Timetable Generator

This module implements a Genetic Algorithm to generate conflict-free,
workload-balanced academic timetables for KTU University.

Chromosome Encoding:
    Each chromosome represents a complete timetable for all classes in a semester.
    Gene = (class_id, subject_id, faculty_id, time_slot_id)
    
Fitness Function considers:
    - Hard constraints (must satisfy): faculty clash, class clash, workload limits, lab continuity
    - Soft constraints (try to satisfy): faculty preferences, workload balance, subject rotation
"""

import random
import copy
from collections import defaultdict
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from django.db.models import Q


@dataclass
class Gene:
    """Represents a single timetable entry"""
    class_id: int
    subject_id: int
    faculty_id: int
    time_slot_id: int
    is_lab: bool = False
    assistant_faculty_id: Optional[int] = None


@dataclass
class Chromosome:
    """Represents a complete timetable solution"""
    genes: List[Gene] = field(default_factory=list)
    fitness: float = 0.0
    
    def copy(self):
        return Chromosome(
            genes=[Gene(**g.__dict__) for g in self.genes],
            fitness=self.fitness
        )


class GeneticAlgorithm:
    """
    Genetic Algorithm for Timetable Generation
    
    Parameters:
        population_size: Number of chromosomes in population
        generations: Maximum number of generations
        crossover_rate: Probability of crossover
        mutation_rate: Probability of mutation
        elite_count: Number of best chromosomes to preserve
        tournament_size: Size of tournament for selection
    """
    
    # Constraint weights
    WEIGHTS = {
        'faculty_clash': -5000,      # Hard: Same faculty in 2 classes at same time
        'class_clash': -1000,        # Hard: Same class has 2 subjects at same time  
        'workload_exceeded': -500,   # Hard: Faculty exceeds max hours
        'lab_continuity': -5000,     # Hard: Lab MUST be in 3 continuous periods
        'lab_timing': -100,          # Soft: Labs should be morning OR afternoon
        'lab_day_clash': -800,       # Hard: Different classes should have labs on different days
        'lab_room_clash': -1500,     # Hard: Same lab subject at same time = room conflict
        'cross_dept_clash': -2000,   # Hard: Faculty already booked in another department
        'two_labs_per_week': -500,   # Hard: Each class must have exactly 2 labs
        'lab_faculty_inconsistent': -2000,  # Hard: All lab hours for a class+subject must have same faculty
        'subject_rotation': -50,     # Soft: Penalize same faculty-subject pairs
        'faculty_preference': 100,   # Soft: Bonus for matching preferences
        'no_preference_match': -800, # Hard: Faculty assigned to subject not in their preferences
        'professor_lab': -1500,      # Hard: Professors should NOT be assigned to lab sessions
        'workload_balance': -30,     # Soft: Penalize uneven distribution
        'workload_under_min': -300,  # Soft: Faculty below their minimum hours
        'consecutive_theory': -300,  # Soft: Penalize same theory subject in consecutive periods
        'faculty_consecutive': -500, # Hard: Faculty should not have back-to-back consecutive classes
        'faculty_multi_theory': -2000, # Hard: Faculty should teach only ONE theory subject per class
        'special_subject_daily': -1000, # Hard: Remedial/Minor/Honour/Elective max once per day per class
    }
    
    def __init__(
        self,
        population_size: int = 100,
        generations: int = 500,
        crossover_rate: float = 0.8,
        mutation_rate: float = 0.1,
        elite_count: int = 5,
        tournament_size: int = 5
    ):
        self.population_size = population_size
        self.generations = generations
        self.crossover_rate = crossover_rate
        self.mutation_rate = mutation_rate
        self.elite_count = elite_count
        self.tournament_size = tournament_size
        
        # Data to be loaded
        self.classes = []
        self.subjects = []
        self.faculties = []
        self.time_slots = []
        self.lab_subjects = []
        self.theory_subjects = []
        self.faculty_preferences = {}
        self.faculty_history = {}  # For subject rotation tracking
        self.faculty_workload_limits = {}
        self.department_id = None  # Department being scheduled
        
        # Mapping for quick lookup
        self.class_subjects = defaultdict(list)  # class_id -> list of subject_ids
        self.subject_info = {}  # subject_id -> {type, hours, etc}
        self.pre_booked_slots = {}  # faculty_id -> set of time_slot_ids (from other departments)
        self.dept_faculty_ids = set()  # Faculty IDs belonging to the home department
        self.faculty_workload_min = {}  # faculty_id -> min hours
        self.faculty_designation = {}   # faculty_id -> designation string
        
    def load_data(self, classes, subjects, faculties, time_slots, 
                  faculty_preferences=None, faculty_history=None,
                  pre_booked_slots=None, department_id=None):
        """Load problem data from Django models
        
        Args:
            pre_booked_slots: Optional dict of faculty_id -> set of time_slot_ids.
                              These are slots already committed in OTHER departments'
                              timetables. The GA will avoid assigning this faculty
                              to these slots.
            department_id: ID of the department being scheduled. Used to determine
                           which faculty are 'home' vs 'cross-department'.
        """
        self.classes = classes
        self.subjects = subjects
        self.faculties = faculties
        self.time_slots = time_slots
        self.department_id = department_id
        
        # Separate subjects by type
        self.lab_subjects = [s for s in subjects if s['subject_type'] == 'LAB']
        self.theory_subjects = [s for s in subjects if s['subject_type'] == 'THEORY']
        
        # Build subject info map
        for s in subjects:
            self.subject_info[s['id']] = s
            # Map subjects to their semester's classes
            for c in classes:
                if c['semester_id'] == s['semester_id']:
                    self.class_subjects[c['id']].append(s['id'])
        
        # Faculty data
        self.faculty_preferences = faculty_preferences or {}
        self.faculty_history = faculty_history or {}
        self.pre_booked_slots = pre_booked_slots or {}
        
        # Identify department faculty vs cross-department faculty
        self.dept_faculty_ids = set()
        for f in faculties:
            if f.get('department_id') == department_id or f.get('department_id') is None:
                self.dept_faculty_ids.add(f['id'])
        
        for f in faculties:
            self.faculty_workload_limits[f['id']] = f['max_hours']
            self.faculty_workload_min[f['id']] = f.get('min_hours', f['max_hours'])
            self.faculty_designation[f['id']] = f.get('designation', '')
    
    def initialize_population(self) -> List[Chromosome]:
        """Create initial random population"""
        population = []
        
        for _ in range(self.population_size):
            chromosome = self._create_random_chromosome()
            population.append(chromosome)
        
        return population
    
    def _create_random_chromosome(self) -> Chromosome:
        """Create a single random but valid chromosome"""
        genes = []
        
        # Track which (day, half) combos are used for labs across ALL classes
        # to distribute labs across different days
        # Format: (day, 'morning'/'afternoon') -> count of classes using it
        global_lab_day_usage = defaultdict(int)
        
        # Track which time slots are used for each lab subject across all classes
        # to prevent room clashes (same lab room used by two classes at same time)
        # Format: lab_subject_id -> set of time_slot_ids
        global_lab_room_usage = defaultdict(set)
        
        # Track faculty schedules across ALL classes to prevent faculty clashes
        # Format: faculty_id -> set of time_slot_ids
        global_faculty_schedule = defaultdict(set)
        
        # Track lab faculty assignments: (class_id, lab_subject_id) -> (main_faculty, assistant_faculty)
        # Ensures the same main faculty handles all lab hours for each class+subject
        class_lab_faculty = {}
        
        # Shuffle class order so different chromosomes try different orderings
        shuffled_classes = list(self.classes)
        random.shuffle(shuffled_classes)
        
        for class_info in shuffled_classes:
            class_id = class_info['id']
            class_subject_ids = self.class_subjects[class_id]
            
            # Get available time slots
            available_slots = [ts['id'] for ts in self.time_slots]
            used_slots = set()
            
            # Track days used for labs by THIS class to avoid same day
            class_lab_days = set()
            
            # First, schedule labs (need 3 continuous periods each, 2 per week)
            lab_subjects_for_class = [
                s_id for s_id in class_subject_ids 
                if self.subject_info[s_id]['subject_type'] == 'LAB'
            ]
            
            for lab_id in lab_subjects_for_class[:2]:  # Max 2 labs per week
                # Find 3 continuous morning or afternoon slots
                # Exclude days already used for labs by this class,
                # and prefer days less used globally
                # Also avoid slots where this same lab subject is already scheduled
                # for another class (implies same physical lab room)
                lab_blocked_slots = global_lab_room_usage.get(lab_id, set())
                lab_slots = self._find_lab_slots(
                    available_slots, used_slots | lab_blocked_slots, 
                    exclude_days=class_lab_days,
                    day_usage=global_lab_day_usage
                )
                if lab_slots:
                    # Reuse same faculty if already assigned for this class+subject
                    if (class_id, lab_id) in class_lab_faculty:
                        main_faculty, assistant_faculty = class_lab_faculty[(class_id, lab_id)]
                    else:
                        # Assign main and assistant faculty
                        # Filter out faculty who are pre-booked OR already scheduled during these lab slots
                        eligible_faculty = self._get_eligible_faculty_for_subject(lab_id)
                        eligible_faculty = [
                            f_id for f_id in eligible_faculty
                            if not any(
                                s_id in self.pre_booked_slots.get(f_id, set()) or
                                s_id in global_faculty_schedule.get(f_id, set())
                                for s_id in lab_slots
                            )
                        ]
                        
                        if len(eligible_faculty) >= 2:
                            main_faculty = random.choice(eligible_faculty)
                            assistant_faculty = random.choice([f for f in eligible_faculty if f != main_faculty])
                        elif len(eligible_faculty) == 1:
                            main_faculty = eligible_faculty[0]
                            assistant_faculty = None
                        else:
                            # Fallback: pick any faculty (will get penalized in fitness)
                            all_faculty_ids = [f['id'] for f in self.faculties]
                            main_faculty = random.choice(all_faculty_ids)
                            assistant_faculty = None
                        
                        # Remember this faculty for future lab sessions of same class+subject
                        class_lab_faculty[(class_id, lab_id)] = (main_faculty, assistant_faculty)
                    
                    # Determine which day and half this lab lands on
                    lab_slot_info = [ts for ts in self.time_slots if ts['id'] == lab_slots[0]]
                    if lab_slot_info:
                        lab_day = lab_slot_info[0]['day']
                        lab_half = 'morning' if lab_slot_info[0]['period'] <= 3 else 'afternoon'
                        class_lab_days.add(lab_day)
                        global_lab_day_usage[(lab_day, lab_half)] += 1
                    
                    for slot_id in lab_slots:
                        genes.append(Gene(
                            class_id=class_id,
                            subject_id=lab_id,
                            faculty_id=main_faculty,
                            time_slot_id=slot_id,
                            is_lab=True,
                            assistant_faculty_id=assistant_faculty
                        ))
                        used_slots.add(slot_id)
                        # Track this lab subject's time slots globally for room clash prevention
                        global_lab_room_usage[lab_id].add(slot_id)
                        # Track faculty schedule globally
                        global_faculty_schedule[main_faculty].add(slot_id)
                        if assistant_faculty:
                            global_faculty_schedule[assistant_faculty].add(slot_id)
            
            # Then, schedule theory subjects
            theory_subjects_for_class = [
                s_id for s_id in class_subject_ids 
                if self.subject_info[s_id]['subject_type'] == 'THEORY'
            ]
            
            # Track which subject is assigned to each slot for this class
            # Used to avoid placing the same subject in consecutive periods
            class_slot_subject = {}  # time_slot_id -> subject_id
            
            # Track which faculty is already assigned to a theory subject in this class
            # A faculty should only teach ONE theory subject per class
            class_faculty_theory = {}  # faculty_id -> theory_subject_id (first assignment)
            
            for subject_id in theory_subjects_for_class:
                hours_needed = self.subject_info[subject_id].get('hours_per_week', 3)
                eligible_faculty = self._get_eligible_faculty_for_subject(subject_id)
                
                if eligible_faculty:
                    # Filter out faculty already assigned to a DIFFERENT theory subject in this class
                    available_faculty = [
                        f_id for f_id in eligible_faculty
                        if f_id not in class_faculty_theory or class_faculty_theory[f_id] == subject_id
                    ]
                    if available_faculty:
                        faculty_id = random.choice(available_faculty)
                    else:
                        # All eligible faculty already have a theory subject in this class
                        # Fall back to eligible (will be penalized in fitness)
                        faculty_id = random.choice(eligible_faculty)
                else:
                    faculty_id = random.choice([f['id'] for f in self.faculties])
                
                # Record this faculty's theory subject assignment for this class
                class_faculty_theory[faculty_id] = subject_id
                
                # Assign hours across the week
                # Avoid slots where this faculty is pre-booked OR already teaching another class
                faculty_blocked = self.pre_booked_slots.get(faculty_id, set()) | global_faculty_schedule.get(faculty_id, set())
                remaining_slots = [s for s in available_slots 
                                   if s not in used_slots and s not in faculty_blocked]
                random.shuffle(remaining_slots)
                
                # If not enough slots excluding blocked, include blocked as fallback
                if len(remaining_slots) < hours_needed:
                    extra = [s for s in available_slots 
                             if s not in used_slots and s in faculty_blocked]
                    remaining_slots.extend(extra)
                
                # Sort remaining slots to prefer slots that don't create
                # consecutive same-subject periods OR consecutive faculty periods
                def _consecutive_penalty(slot_id):
                    """Return penalty score: higher means less desirable.
                    Penalizes same-subject consecutive AND faculty consecutive."""
                    slot_info = next((ts for ts in self.time_slots if ts['id'] == slot_id), None)
                    if not slot_info:
                        return 0
                    penalty = 0
                    day, period = slot_info['day'], slot_info['period']
                    for adj_period in (period - 1, period + 1):
                        adj_slot = next(
                            (ts['id'] for ts in self.time_slots
                             if ts['day'] == day and ts['period'] == adj_period),
                            None
                        )
                        if adj_slot:
                            # Penalize same subject in consecutive periods
                            if class_slot_subject.get(adj_slot) == subject_id:
                                penalty += 2
                            # Penalize faculty teaching in consecutive periods
                            if adj_slot in global_faculty_schedule.get(faculty_id, set()):
                                penalty += 1
                    return penalty
                
                remaining_slots.sort(key=lambda s: (_consecutive_penalty(s), random.random()))
                
                slots_assigned = 0
                for slot_id in remaining_slots:
                    if slots_assigned >= hours_needed:
                        break
                    genes.append(Gene(
                        class_id=class_id,
                        subject_id=subject_id,
                        faculty_id=faculty_id,
                        time_slot_id=slot_id,
                        is_lab=False
                    ))
                    used_slots.add(slot_id)
                    class_slot_subject[slot_id] = subject_id
                    global_faculty_schedule[faculty_id].add(slot_id)
                    slots_assigned += 1
            
            # ── Fill remaining empty periods ──────────────────────────
            # After scheduling all subjects with their required hours,
            # distribute extra hours among theory subjects to fill all 35 slots.
            # Also include ELECTIVE and RMH (Remedial/Minor/Honour) subjects if available.
            fillable_subjects = [
                s_id for s_id in class_subject_ids 
                if self.subject_info[s_id]['subject_type'] in ('THEORY', 'ELECTIVE', 'RMH')
            ]
            
            if fillable_subjects:
                remaining_slots = [s for s in available_slots if s not in used_slots]
                random.shuffle(remaining_slots)
                
                # Track which faculty was assigned to each subject
                subject_faculty_map = {}
                for gene in genes:
                    if gene.class_id == class_id and not gene.is_lab:
                        subject_faculty_map[gene.subject_id] = gene.faculty_id
                
                # Helper: check if placing a subject at a slot creates consecutive issues
                def _would_be_consecutive(slot_id, subj_id, fac_id):
                    slot_info = next((ts for ts in self.time_slots if ts['id'] == slot_id), None)
                    if not slot_info:
                        return False
                    day, period = slot_info['day'], slot_info['period']
                    for adj_period in (period - 1, period + 1):
                        adj_slot = next(
                            (ts['id'] for ts in self.time_slots
                             if ts['day'] == day and ts['period'] == adj_period),
                            None
                        )
                        if adj_slot:
                            # Same subject consecutive
                            if class_slot_subject.get(adj_slot) == subj_id:
                                return True
                            # Faculty consecutive (teaching any class)
                            if adj_slot in global_faculty_schedule.get(fac_id, set()):
                                return True
                    return False
                
                # Round-robin fill remaining slots with theory/elective subjects
                # CHECK for faculty clashes AND consecutive same-subject before each assignment
                # Build a list of (subject, eligible_faculty) pairs for flexible assignment
                subject_eligible_map = {}
                for s_id in fillable_subjects:
                    subject_eligible_map[s_id] = self._get_eligible_faculty_for_subject(s_id)
                
                fill_idx = 0
                slot_queue = list(remaining_slots)
                
                while slot_queue:
                    slot_id = slot_queue[0]
                    assigned = False
                    
                    # Try each subject for this slot
                    for attempt in range(len(fillable_subjects)):
                        subject_id = fillable_subjects[(fill_idx + attempt) % len(fillable_subjects)]
                        faculty_id = subject_faculty_map.get(subject_id)
                        
                        if not faculty_id:
                            eligible = subject_eligible_map.get(subject_id, [])
                            faculty_id = random.choice(eligible) if eligible else random.choice([f['id'] for f in self.faculties])
                            subject_faculty_map[subject_id] = faculty_id
                        
                        # Check primary faculty first
                        faculty_clash = slot_id in global_faculty_schedule.get(faculty_id, set())
                        
                        if not faculty_clash:
                            # Good — no clash with this subject's primary faculty
                            consec_clash = _would_be_consecutive(slot_id, subject_id, faculty_id)
                            if not consec_clash:
                                # Perfect slot
                                slot_queue.pop(0)
                                genes.append(Gene(
                                    class_id=class_id,
                                    subject_id=subject_id,
                                    faculty_id=faculty_id,
                                    time_slot_id=slot_id,
                                    is_lab=False
                                ))
                                used_slots.add(slot_id)
                                class_slot_subject[slot_id] = subject_id
                                global_faculty_schedule[faculty_id].add(slot_id)
                                fill_idx = (fill_idx + attempt + 1) % len(fillable_subjects)
                                assigned = True
                                break
                            else:
                                # Consecutive clash but no faculty clash — acceptable fallback
                                # Keep looking but remember this as backup
                                pass
                        else:
                            # Faculty clash — try an ALTERNATE faculty for this subject
                            eligible = subject_eligible_map.get(subject_id, [])
                            alt_faculty = [f for f in eligible 
                                          if f != faculty_id and 
                                          slot_id not in global_faculty_schedule.get(f, set())]
                            if alt_faculty:
                                alt_fac = random.choice(alt_faculty)
                                slot_queue.pop(0)
                                genes.append(Gene(
                                    class_id=class_id,
                                    subject_id=subject_id,
                                    faculty_id=alt_fac,
                                    time_slot_id=slot_id,
                                    is_lab=False
                                ))
                                used_slots.add(slot_id)
                                class_slot_subject[slot_id] = subject_id
                                global_faculty_schedule[alt_fac].add(slot_id)
                                fill_idx = (fill_idx + attempt + 1) % len(fillable_subjects)
                                assigned = True
                                break
                    
                    if not assigned:
                        # Last resort: pick the subject with least clashing faculty
                        slot_queue.pop(0)
                        best_subj = fillable_subjects[fill_idx % len(fillable_subjects)]
                        best_fac = subject_faculty_map.get(best_subj)
                        if not best_fac:
                            eligible = subject_eligible_map.get(best_subj, [])
                            best_fac = random.choice(eligible) if eligible else random.choice([f['id'] for f in self.faculties])
                        # Try to find ANY non-clashing faculty
                        all_eligible = subject_eligible_map.get(best_subj, [])
                        non_clash_fac = [f for f in all_eligible
                                        if slot_id not in global_faculty_schedule.get(f, set())]
                        if non_clash_fac:
                            best_fac = random.choice(non_clash_fac)
                        genes.append(Gene(
                            class_id=class_id,
                            subject_id=best_subj,
                            faculty_id=best_fac,
                            time_slot_id=slot_id,
                            is_lab=False
                        ))
                        used_slots.add(slot_id)
                        class_slot_subject[slot_id] = best_subj
                        global_faculty_schedule[best_fac].add(slot_id)
                        fill_idx = (fill_idx + 1) % len(fillable_subjects)
        
        return Chromosome(genes=genes)
    
    def _find_lab_slots(self, available_slots: List[int], used_slots: set,
                        exclude_days: set = None, day_usage: dict = None) -> List[int]:
        """Find 3 continuous periods for a lab session on a suitable day.
        
        Args:
            available_slots: List of all slot IDs
            used_slots: Set of slot IDs already used by this class
            exclude_days: Days to avoid (already used for labs by this class)
            day_usage: Global dict of (day, half) -> count for load balancing
        """
        if exclude_days is None:
            exclude_days = set()
        if day_usage is None:
            day_usage = {}
        
        # Group slots by day
        slots_by_day = defaultdict(list)
        for slot in self.time_slots:
            if slot['id'] in available_slots and slot['id'] not in used_slots:
                slots_by_day[slot['day']].append(slot)
        
        # Build list of (day, half, slot_ids) candidates
        candidates = []
        for day, day_slots in slots_by_day.items():
            day_slots.sort(key=lambda x: x['period'])
            
            # Try morning slots (periods 1, 2, 3)
            morning_slots = [s for s in day_slots if s['period'] <= 3]
            if len(morning_slots) >= 3:
                periods = [s['period'] for s in morning_slots]
                if 1 in periods and 2 in periods and 3 in periods:
                    slot_ids = [s['id'] for s in morning_slots if s['period'] <= 3][:3]
                    candidates.append((day, 'morning', slot_ids))
            
            # Try afternoon slots (periods 5, 6, 7)
            afternoon_slots = [s for s in day_slots if s['period'] >= 5]
            if len(afternoon_slots) >= 3:
                periods = [s['period'] for s in afternoon_slots]
                if 5 in periods and 6 in periods and 7 in periods:
                    slot_ids = [s['id'] for s in afternoon_slots if s['period'] >= 5][:3]
                    candidates.append((day, 'afternoon', slot_ids))
        
        if not candidates:
            # Fallback: find any 3 continuous slots
            days = list(slots_by_day.keys())
            random.shuffle(days)
            for day in days:
                day_slots = slots_by_day[day]
                if len(day_slots) >= 3:
                    day_slots.sort(key=lambda x: x['period'])
                    for i in range(len(day_slots) - 2):
                        if day_slots[i+1]['period'] == day_slots[i]['period'] + 1 and \
                           day_slots[i+2]['period'] == day_slots[i]['period'] + 2:
                            return [day_slots[i]['id'], day_slots[i+1]['id'], day_slots[i+2]['id']]
            return []
        
        # Prioritize: first candidates NOT on excluded days, then by least global usage
        preferred = [c for c in candidates if c[0] not in exclude_days]
        fallback = [c for c in candidates if c[0] in exclude_days]
        
        if preferred:
            # Sort by global usage (least used first) then randomize ties
            preferred.sort(key=lambda c: (day_usage.get((c[0], c[1]), 0), random.random()))
            return preferred[0][2]
        elif fallback:
            fallback.sort(key=lambda c: (day_usage.get((c[0], c[1]), 0), random.random()))
            return fallback[0][2]
        
        return []
    
    def _get_eligible_faculty_for_subject(self, subject_id: int) -> List[int]:
        """Get faculty IDs who can teach a subject based on preferences and department.
        
        STRICT rules:
        1. Faculty who explicitly prefer this subject -> use ONLY them
        2. If no faculty has this subject in preferences AND this is a
           home-department subject -> use only home department faculty
        3. NEVER fall back to all faculty (avoids wrong department assignments)
        4. Professors are EXCLUDED from lab subjects
        """
        subject = self.subject_info.get(subject_id, {})
        subject_code = subject.get('code', '')
        is_lab = subject.get('subject_type') == 'LAB'
        
        # First priority: faculty who explicitly prefer this subject
        preferred_faculty = []
        for faculty in self.faculties:
            preferences = self.faculty_preferences.get(faculty['id'], [])
            if subject_code in preferences:
                # Exclude Professors from lab subjects
                if is_lab and self.faculty_designation.get(faculty['id']) == 'PROFESSOR':
                    continue
                preferred_faculty.append(faculty['id'])
        
        # If any faculty explicitly prefers this subject, use ONLY them
        if preferred_faculty:
            return preferred_faculty
        
        # No one has this subject in preferences:
        # Use ONLY home department faculty (ensures CS subjects -> CS faculty etc.)
        if self.dept_faculty_ids:
            eligible = list(self.dept_faculty_ids)
            # Exclude Professors from lab subjects
            if is_lab:
                eligible = [f_id for f_id in eligible
                            if self.faculty_designation.get(f_id) != 'PROFESSOR']
            if eligible:
                return eligible
        
        # Absolute fallback (should not happen in a properly configured system)
        all_faculty = [f['id'] for f in self.faculties]
        if is_lab:
            all_faculty = [f_id for f_id in all_faculty
                           if self.faculty_designation.get(f_id) != 'PROFESSOR']
        return all_faculty
    
    def calculate_fitness(self, chromosome: Chromosome) -> float:
        """Calculate fitness score for a chromosome"""
        fitness = 0.0
        
        # Track violations
        faculty_schedule = defaultdict(set)  # faculty_id -> set of time_slot_ids
        class_schedule = defaultdict(set)    # class_id -> set of time_slot_ids
        faculty_hours = defaultdict(int)      # faculty_id -> total hours
        class_labs = defaultdict(list)        # class_id -> list of lab genes
        
        for gene in chromosome.genes:
            # Check faculty clash
            if gene.time_slot_id in faculty_schedule[gene.faculty_id]:
                fitness += self.WEIGHTS['faculty_clash']
            faculty_schedule[gene.faculty_id].add(gene.time_slot_id)
            
            # Check assistant faculty clash too
            if gene.assistant_faculty_id:
                if gene.time_slot_id in faculty_schedule[gene.assistant_faculty_id]:
                    fitness += self.WEIGHTS['faculty_clash']
                faculty_schedule[gene.assistant_faculty_id].add(gene.time_slot_id)
            
            # Check class clash
            if gene.time_slot_id in class_schedule[gene.class_id]:
                fitness += self.WEIGHTS['class_clash']
            class_schedule[gene.class_id].add(gene.time_slot_id)
            
            # Track faculty hours
            faculty_hours[gene.faculty_id] += 1
            if gene.assistant_faculty_id:
                faculty_hours[gene.assistant_faculty_id] += 1
            
            # Track labs
            if gene.is_lab:
                class_labs[gene.class_id].append(gene)
            
            # Check faculty preference match
            preferences = self.faculty_preferences.get(gene.faculty_id, [])
            subject_code = self.subject_info.get(gene.subject_id, {}).get('code', '')
            if subject_code in preferences:
                fitness += self.WEIGHTS['faculty_preference']
            else:
                # Faculty is teaching a subject NOT in their preferences
                # Only penalize if the faculty has preferences set (i.e. is a specialist)
                if preferences:
                    fitness += self.WEIGHTS['no_preference_match']
            
            # Check if Professor is assigned to a lab
            if gene.is_lab and self.faculty_designation.get(gene.faculty_id) == 'PROFESSOR':
                fitness += self.WEIGHTS['professor_lab']
            if gene.is_lab and gene.assistant_faculty_id and self.faculty_designation.get(gene.assistant_faculty_id) == 'PROFESSOR':
                fitness += self.WEIGHTS['professor_lab']
            
            # Check cross-department clash (faculty pre-booked in another dept)
            if gene.faculty_id in self.pre_booked_slots:
                if gene.time_slot_id in self.pre_booked_slots[gene.faculty_id]:
                    fitness += self.WEIGHTS['cross_dept_clash']
            if gene.assistant_faculty_id and gene.assistant_faculty_id in self.pre_booked_slots:
                if gene.time_slot_id in self.pre_booked_slots[gene.assistant_faculty_id]:
                    fitness += self.WEIGHTS['cross_dept_clash']
        
        # Check workload limits (both min and max)
        for faculty_id, hours in faculty_hours.items():
            max_hours = self.faculty_workload_limits.get(faculty_id, 20)
            min_hours = self.faculty_workload_min.get(faculty_id, max_hours)
            if hours > max_hours:
                fitness += self.WEIGHTS['workload_exceeded'] * (hours - max_hours)
            elif hours < min_hours:
                fitness += self.WEIGHTS['workload_under_min'] * (min_hours - hours)
        
        # ── Faculty multi-theory penalty ──────────────────────────
        # Each faculty should teach at most ONE theory subject per class.
        # Labs by the same faculty in the same class are OK.
        faculty_class_theory = defaultdict(lambda: defaultdict(set))  # faculty_id -> class_id -> set of theory subject_ids
        for gene in chromosome.genes:
            if not gene.is_lab:
                faculty_class_theory[gene.faculty_id][gene.class_id].add(gene.subject_id)
        
        for faculty_id, class_map in faculty_class_theory.items():
            for class_id, theory_subjects in class_map.items():
                if len(theory_subjects) > 1:
                    # Penalize each extra theory subject beyond the first
                    fitness += self.WEIGHTS['faculty_multi_theory'] * (len(theory_subjects) - 1)
        
        # ── Special subject daily limit ──────────────────────────
        # ELECTIVE and RMH (Remedial/Minor/Honour) subjects: max ONE per day per class
        SPECIAL_TYPES = {'ELECTIVE', 'RMH'}
        class_day_special = defaultdict(lambda: defaultdict(int))  # class_id -> day -> count
        for gene in chromosome.genes:
            subj_type = self.subject_info.get(gene.subject_id, {}).get('subject_type', '')
            if subj_type in SPECIAL_TYPES:
                slot_info = next((ts for ts in self.time_slots if ts['id'] == gene.time_slot_id), None)
                if slot_info:
                    class_day_special[gene.class_id][slot_info['day']] += 1
        
        for class_id, day_counts in class_day_special.items():
            for day, count in day_counts.items():
                if count > 1:
                    fitness += self.WEIGHTS['special_subject_daily'] * (count - 1)
        
        # Check lab constraints
        for class_id, lab_genes in class_labs.items():
            # Group by subject to check continuity
            lab_subjects_scheduled = set(g.subject_id for g in lab_genes)
            
            for lab_subject_id in lab_subjects_scheduled:
                subject_lab_genes = [g for g in lab_genes if g.subject_id == lab_subject_id]
                slot_ids = [g.time_slot_id for g in subject_lab_genes]
                
                # Lab MUST have exactly 3 genes
                if len(slot_ids) != 3:
                    fitness += self.WEIGHTS['lab_continuity'] * 2  # Extra penalty for wrong count
                else:
                    # Check lab continuity (same day, consecutive periods)
                    if not self._check_lab_continuity(slot_ids):
                        fitness += self.WEIGHTS['lab_continuity']
                    
                    # Check lab timing (should be all morning or all afternoon)
                    if not self._check_lab_timing(slot_ids):
                        fitness += self.WEIGHTS['lab_timing']
        
        # Check lab day distribution across classes
        # Penalize when multiple classes have labs on the same day+half
        lab_day_half_usage = defaultdict(int)  # (day, half) -> count
        for class_id, lab_genes in class_labs.items():
            lab_days_for_class = set()
            for gene in lab_genes:
                slot_info = next((ts for ts in self.time_slots if ts['id'] == gene.time_slot_id), None)
                if slot_info:
                    half = 'morning' if slot_info['period'] <= 3 else 'afternoon'
                    day_half = (slot_info['day'], half)
                    if day_half not in lab_days_for_class:
                        lab_days_for_class.add(day_half)
                        lab_day_half_usage[day_half] += 1
        
        # Penalize each (day, half) that has more than 1 class with labs
        for day_half, count in lab_day_half_usage.items():
            if count > 1:
                fitness += self.WEIGHTS['lab_day_clash'] * (count - 1)
        
        # Check lab faculty consistency: all lab genes for same (class, subject) must have same faculty
        for class_id, lab_genes in class_labs.items():
            lab_faculty_by_subject = defaultdict(set)
            for g in lab_genes:
                lab_faculty_by_subject[g.subject_id].add(g.faculty_id)
            for subj_id, faculty_set in lab_faculty_by_subject.items():
                if len(faculty_set) > 1:
                    fitness += self.WEIGHTS['lab_faculty_inconsistent'] * (len(faculty_set) - 1)
        
        # Check lab room clashes (same lab subject, different classes, same time slot)
        # If two classes have the same lab subject at the same time, they'd need the same room
        lab_slot_usage = defaultdict(lambda: defaultdict(set))  # subject_id -> time_slot_id -> set of class_ids
        for gene in chromosome.genes:
            if gene.is_lab:
                lab_slot_usage[gene.subject_id][gene.time_slot_id].add(gene.class_id)
        
        for subject_id, slot_classes in lab_slot_usage.items():
            for slot_id, class_ids in slot_classes.items():
                if len(class_ids) > 1:
                    fitness += self.WEIGHTS['lab_room_clash'] * (len(class_ids) - 1)
        
        # Check workload balance (soft constraint)
        if faculty_hours:
            avg_hours = sum(faculty_hours.values()) / len(faculty_hours)
            for hours in faculty_hours.values():
                deviation = abs(hours - avg_hours)
                if deviation > 5:
                    fitness += self.WEIGHTS['workload_balance'] * (deviation - 5)
        
        # Subject rotation penalty
        for gene in chromosome.genes:
            history = self.faculty_history.get(gene.faculty_id, [])
            subject_code = self.subject_info.get(gene.subject_id, {}).get('code', '')
            if subject_code in history:
                fitness += self.WEIGHTS['subject_rotation']
        
        # ── Consecutive same-theory penalty ──────────────────────────
        # For each class, check each day for same theory subject in adjacent periods
        class_day_genes = defaultdict(lambda: defaultdict(list))  # class_id -> day -> list of (period, gene)
        for gene in chromosome.genes:
            if not gene.is_lab:
                slot_info = next((ts for ts in self.time_slots if ts['id'] == gene.time_slot_id), None)
                if slot_info:
                    class_day_genes[gene.class_id][slot_info['day']].append(
                        (slot_info['period'], gene)
                    )
        
        for class_id, day_map in class_day_genes.items():
            for day, period_genes in day_map.items():
                # Sort by period
                period_genes.sort(key=lambda x: x[0])
                for i in range(len(period_genes) - 1):
                    curr_period, curr_gene = period_genes[i]
                    next_period, next_gene = period_genes[i + 1]
                    # Check if periods are consecutive and same subject
                    if next_period == curr_period + 1 and curr_gene.subject_id == next_gene.subject_id:
                        fitness += self.WEIGHTS['consecutive_theory']
        
        # ── Faculty consecutive class penalty ──────────────────────────
        # Penalize any faculty member who teaches in back-to-back consecutive
        # periods on the same day. Lab blocks (3 consecutive periods) are
        # expected and excluded from this penalty.
        faculty_day_periods = defaultdict(lambda: defaultdict(list))  # faculty_id -> day -> [(period, is_lab, subject_id)]
        for gene in chromosome.genes:
            slot_info = next((ts for ts in self.time_slots if ts['id'] == gene.time_slot_id), None)
            if slot_info:
                faculty_day_periods[gene.faculty_id][slot_info['day']].append(
                    (slot_info['period'], gene.is_lab, gene.subject_id)
                )
                # Also check assistant faculty
                if gene.assistant_faculty_id:
                    faculty_day_periods[gene.assistant_faculty_id][slot_info['day']].append(
                        (slot_info['period'], gene.is_lab, gene.subject_id)
                    )
        
        for faculty_id, day_map in faculty_day_periods.items():
            for day, period_list in day_map.items():
                period_list.sort(key=lambda x: x[0])
                for i in range(len(period_list) - 1):
                    curr_period, curr_is_lab, curr_subj = period_list[i]
                    next_period, next_is_lab, next_subj = period_list[i + 1]
                    # Skip if both are lab sessions of the same subject
                    # (labs naturally span 3 consecutive periods)
                    if curr_is_lab and next_is_lab and curr_subj == next_subj:
                        continue
                    if next_period == curr_period + 1:
                        fitness += self.WEIGHTS['faculty_consecutive']
        
        chromosome.fitness = fitness
        return fitness
    
    def _check_lab_continuity(self, slot_ids: List[int]) -> bool:
        """Check if lab slots are 3 continuous periods"""
        if len(slot_ids) != 3:
            return False
        
        slots = [ts for ts in self.time_slots if ts['id'] in slot_ids]
        if len(slots) != 3:
            return False
        
        # All same day
        days = set(s['day'] for s in slots)
        if len(days) != 1:
            return False
        
        # Continuous periods
        periods = sorted(s['period'] for s in slots)
        return periods[1] == periods[0] + 1 and periods[2] == periods[1] + 1
    
    def _check_lab_timing(self, slot_ids: List[int]) -> bool:
        """Check if all lab slots are in morning or all in afternoon"""
        slots = [ts for ts in self.time_slots if ts['id'] in slot_ids]
        
        # Check if all morning (periods 1-3) or all afternoon (periods 5-7)
        all_morning = all(s['period'] <= 3 for s in slots)
        all_afternoon = all(s['period'] >= 5 for s in slots)
        
        return all_morning or all_afternoon
    
    def _repair_labs(self, chromosome: Chromosome) -> Chromosome:
        """Repair broken lab blocks to ensure 3 continuous periods on the same day"""
        # Group genes by class
        genes_by_class = defaultdict(list)
        for gene in chromosome.genes:
            genes_by_class[gene.class_id].append(gene)
        
        for class_id, class_genes in genes_by_class.items():
            # Find lab genes grouped by subject
            lab_genes_by_subject = defaultdict(list)
            non_lab_genes = []
            for gene in class_genes:
                if gene.is_lab:
                    lab_genes_by_subject[gene.subject_id].append(gene)
                else:
                    non_lab_genes.append(gene)
            
            for subject_id, lab_genes in lab_genes_by_subject.items():
                if len(lab_genes) != 3:
                    continue
                
                slot_ids = [g.time_slot_id for g in lab_genes]
                
                # Check if already continuous
                if self._check_lab_continuity(slot_ids):
                    continue
                
                # Lab is broken - repair it by finding 3 new continuous slots
                # Collect all slots used by THIS class
                all_used_slots = set()
                for gene in class_genes:
                    if gene.subject_id != subject_id or not gene.is_lab:
                        all_used_slots.add(gene.time_slot_id)
                
                available_slots = [ts['id'] for ts in self.time_slots]
                new_lab_slots = self._find_lab_slots(available_slots, all_used_slots)
                
                if new_lab_slots and len(new_lab_slots) == 3:
                    # Check we're not stealing slots from other subjects in same class
                    conflict = False
                    for slot_id in new_lab_slots:
                        if slot_id in all_used_slots:
                            conflict = True
                            break
                    
                    if not conflict:
                        # Reassign the lab genes to the new continuous slots
                        # Also ensure all genes share the same faculty (use first gene's faculty)
                        main_fac = lab_genes[0].faculty_id
                        asst_fac = lab_genes[0].assistant_faculty_id
                        for i, gene in enumerate(lab_genes):
                            gene.time_slot_id = new_lab_slots[i]
                            gene.faculty_id = main_fac
                            gene.assistant_faculty_id = asst_fac
        
        return chromosome
    
    def _repair_faculty_clashes(self, chromosome: Chromosome) -> Chromosome:
        """Repair faculty clashes by swapping time slots within the same class.
        
        When a faculty member is double-booked (assigned to two different classes
        at the same time slot), this method attempts to fix it by swapping the
        clashing gene's time slot with another non-lab gene from the same class
        that doesn't create a new clash.
        """
        max_repair_iterations = 10  # Prevent infinite loops
        
        for iteration in range(max_repair_iterations):
            # Build faculty schedule: faculty_id -> {time_slot_id: [gene_indices]}
            faculty_slot_genes = defaultdict(lambda: defaultdict(list))
            for idx, gene in enumerate(chromosome.genes):
                faculty_slot_genes[gene.faculty_id][gene.time_slot_id].append(idx)
                if gene.assistant_faculty_id:
                    faculty_slot_genes[gene.assistant_faculty_id][gene.time_slot_id].append(idx)
            
            # Find clashes
            clashes_found = False
            for fac_id, slot_map in faculty_slot_genes.items():
                for slot_id, gene_indices in slot_map.items():
                    if len(gene_indices) <= 1:
                        continue
                    
                    clashes_found = True
                    
                    # Try to fix: pick one of the clashing genes (prefer non-lab)
                    non_lab_clash_indices = [i for i in gene_indices
                                            if not chromosome.genes[i].is_lab]
                    if not non_lab_clash_indices:
                        continue  # Can't fix lab clashes easily
                    
                    clash_idx = random.choice(non_lab_clash_indices)
                    clash_gene = chromosome.genes[clash_idx]
                    
                    # Find non-lab genes from the SAME class with different time slots
                    # that won't create a new clash for this faculty
                    swap_candidates = []
                    for i, g in enumerate(chromosome.genes):
                        if (g.class_id == clash_gene.class_id and 
                            i != clash_idx and 
                            not g.is_lab and
                            g.time_slot_id != slot_id):
                            # Check if swapping would create a NEW clash for fac_id
                            new_slot = g.time_slot_id
                            would_clash = len(faculty_slot_genes[fac_id].get(new_slot, [])) > 0
                            if not would_clash:
                                swap_candidates.append(i)
                    
                    if swap_candidates:
                        swap_idx = random.choice(swap_candidates)
                        # Swap time slots
                        chromosome.genes[clash_idx].time_slot_id, \
                            chromosome.genes[swap_idx].time_slot_id = \
                            chromosome.genes[swap_idx].time_slot_id, \
                            chromosome.genes[clash_idx].time_slot_id
                        break  # Restart clash detection after fix
            
            if not clashes_found:
                break  # No more clashes
        
        return chromosome
    
    def tournament_selection(self, population: List[Chromosome]) -> Chromosome:
        """Select a chromosome using tournament selection"""
        tournament = random.sample(population, min(self.tournament_size, len(population)))
        return max(tournament, key=lambda c: c.fitness)
    
    def crossover(self, parent1: Chromosome, parent2: Chromosome) -> Tuple[Chromosome, Chromosome]:
        """Partially Mapped Crossover (PMX) for timetables"""
        if random.random() > self.crossover_rate:
            return parent1.copy(), parent2.copy()
        
        child1 = parent1.copy()
        child2 = parent2.copy()
        
        # Group genes by class for structured crossover
        p1_by_class = defaultdict(list)
        p2_by_class = defaultdict(list)
        
        for gene in parent1.genes:
            p1_by_class[gene.class_id].append(gene)
        for gene in parent2.genes:
            p2_by_class[gene.class_id].append(gene)
        
        # Swap genes for random half of the classes
        all_classes = list(set(p1_by_class.keys()) | set(p2_by_class.keys()))
        classes_to_swap = random.sample(all_classes, len(all_classes) // 2)
        
        child1_genes = []
        child2_genes = []
        
        for class_id in all_classes:
            if class_id in classes_to_swap:
                child1_genes.extend([Gene(**g.__dict__) for g in p2_by_class.get(class_id, [])])
                child2_genes.extend([Gene(**g.__dict__) for g in p1_by_class.get(class_id, [])])
            else:
                child1_genes.extend([Gene(**g.__dict__) for g in p1_by_class.get(class_id, [])])
                child2_genes.extend([Gene(**g.__dict__) for g in p2_by_class.get(class_id, [])])
        
        child1.genes = child1_genes
        child2.genes = child2_genes
        
        return child1, child2
    
    def mutate(self, chromosome: Chromosome) -> Chromosome:
        """Apply mutation operators"""
        if random.random() > self.mutation_rate:
            return chromosome
        
        mutated = chromosome.copy()
        
        # Choose mutation type
        mutation_type = random.choice(['swap_slot', 'change_faculty', 'swap_subjects', 'resolve_clash'])
        
        if not mutated.genes:
            return mutated
        
        if mutation_type == 'swap_slot':
            # Swap time slots between two NON-LAB genes of the same class
            non_lab_genes = [g for g in mutated.genes if not g.is_lab]
            if non_lab_genes:
                gene1 = random.choice(non_lab_genes)
                same_class_genes = [g for g in mutated.genes 
                                   if g.class_id == gene1.class_id and g != gene1 and not g.is_lab]
                if same_class_genes:
                    gene2 = random.choice(same_class_genes)
                    gene1.time_slot_id, gene2.time_slot_id = gene2.time_slot_id, gene1.time_slot_id
        
        elif mutation_type == 'change_faculty':
            # Change faculty for a random gene
            gene = random.choice(mutated.genes)
            eligible = self._get_eligible_faculty_for_subject(gene.subject_id)
            if eligible:
                new_faculty = random.choice(eligible)
                if gene.is_lab:
                    # Change ALL lab genes for this class+subject to keep consistency
                    for g in mutated.genes:
                        if g.class_id == gene.class_id and g.subject_id == gene.subject_id and g.is_lab:
                            g.faculty_id = new_faculty
                else:
                    gene.faculty_id = new_faculty
        
        elif mutation_type == 'swap_subjects':
            # Swap subjects in the same time slot (different classes)
            gene1 = random.choice(mutated.genes)
            same_slot_genes = [g for g in mutated.genes 
                              if g.time_slot_id == gene1.time_slot_id and g.class_id != gene1.class_id]
            if same_slot_genes:
                gene2 = random.choice(same_slot_genes)
                gene1.faculty_id, gene2.faculty_id = gene2.faculty_id, gene1.faculty_id
        
        elif mutation_type == 'resolve_clash':
            # Targeted mutation: find a faculty clash and try to fix it
            faculty_slots = defaultdict(list)  # faculty_id -> [(gene_index, time_slot_id)]
            for idx, gene in enumerate(mutated.genes):
                faculty_slots[gene.faculty_id].append((idx, gene.time_slot_id))
            
            # Find a clashing faculty
            for fac_id, slot_list in faculty_slots.items():
                slot_ids = [s for _, s in slot_list]
                seen = set()
                clash_indices = []
                for idx, slot_id in slot_list:
                    if slot_id in seen:
                        clash_indices.append(idx)
                    seen.add(slot_id)
                
                if clash_indices:
                    # Pick one clashing gene and try to swap its slot
                    clash_idx = random.choice(clash_indices)
                    clash_gene = mutated.genes[clash_idx]
                    if not clash_gene.is_lab:
                        # Find another non-lab gene of the same class to swap with
                        swap_candidates = [
                            i for i, g in enumerate(mutated.genes)
                            if g.class_id == clash_gene.class_id 
                            and i != clash_idx
                            and not g.is_lab
                            and g.time_slot_id not in slot_ids  # swap target not in clash set
                        ]
                        if swap_candidates:
                            swap_idx = random.choice(swap_candidates)
                            clash_gene.time_slot_id, mutated.genes[swap_idx].time_slot_id = \
                                mutated.genes[swap_idx].time_slot_id, clash_gene.time_slot_id
                    break  # Fix one clash per mutation
        
        return mutated
    
    def evolve(self, callback=None) -> Tuple[Chromosome, List[float]]:
        """
        Main GA loop
        
        Args:
            callback: Optional function called each generation with (generation, best_fitness)
        
        Returns:
            Tuple of (best_chromosome, fitness_history)
        """
        # Initialize population
        population = self.initialize_population()
        
        # Evaluate initial fitness
        for chromosome in population:
            self.calculate_fitness(chromosome)
        
        fitness_history = []
        best_ever = max(population, key=lambda c: c.fitness)
        
        for generation in range(self.generations):
            # Sort by fitness
            population.sort(key=lambda c: c.fitness, reverse=True)
            
            # Track best
            current_best = population[0]
            if current_best.fitness > best_ever.fitness:
                best_ever = current_best.copy()
            
            fitness_history.append(current_best.fitness)
            
            if callback:
                callback(generation, current_best.fitness)
            
            # Early termination if fitness is good enough
            if current_best.fitness >= 0:
                break
            
            # Create new population
            new_population = []
            
            # Elitism - keep best chromosomes
            for i in range(self.elite_count):
                new_population.append(population[i].copy())
            
            # Generate rest through selection, crossover, mutation
            while len(new_population) < self.population_size:
                parent1 = self.tournament_selection(population)
                parent2 = self.tournament_selection(population)
                
                child1, child2 = self.crossover(parent1, parent2)
                
                child1 = self.mutate(child1)
                child2 = self.mutate(child2)
                
                # Repair broken lab blocks and faculty clashes after crossover/mutation
                child1 = self._repair_labs(child1)
                child2 = self._repair_labs(child2)
                child1 = self._repair_faculty_clashes(child1)
                child2 = self._repair_faculty_clashes(child2)
                
                self.calculate_fitness(child1)
                self.calculate_fitness(child2)
                
                new_population.append(child1)
                if len(new_population) < self.population_size:
                    new_population.append(child2)
            
            population = new_population
        
        return best_ever, fitness_history


def generate_timetable(semester_id: int, semester_instance: str):
    """
    Main entry point for timetable generation
    
    Args:
        semester_id: ID of the semester to generate timetable for
        semester_instance: e.g., "2024-ODD"
    
    Returns:
        Dictionary with timetable data and generation stats
    """
    from core.models import (
        ClassSection, Subject, Faculty, TimeSlot, 
        FacultySubjectAssignment, TimetableEntry
    )
    
    # Load data from database
    classes = list(ClassSection.objects.filter(
        semester_id=semester_id
    ).values('id', 'name', 'semester_id'))
    
    subjects = list(Subject.objects.filter(
        semester_id=semester_id
    ).values('id', 'name', 'code', 'subject_type', 'lecture_hours', 'tutorial_hours', 'practical_hours', 'semester_id'))
    
    # Compute hours_per_week in Python (cannot use @property in .values())
    valid_subjects = []
    for subject in subjects:
        total_hours = subject['lecture_hours'] + subject['tutorial_hours'] + subject['practical_hours']
        subject['hours_per_week'] = total_hours
        
        if total_hours == 0:
            print(f"WARNING: Subject {subject['code']} ({subject['name']}) has zero total hours - skipping from timetable generation")
        else:
            valid_subjects.append(subject)
    
    # Replace subjects list with filtered valid subjects
    if len(valid_subjects) < len(subjects):
        print(f"Skipped {len(subjects) - len(valid_subjects)} subject(s) with zero hours")
    subjects = valid_subjects
    
    faculties = list(Faculty.objects.filter(
        is_active=True
    ).values('id', 'name', 'designation', 'preferences'))
    
    # Add min/max hours to faculty data based on designation
    for f in faculties:
        limits = Faculty.WORKLOAD_LIMITS.get(f['designation'], (20, 20))
        if isinstance(limits, tuple):
            f['min_hours'] = limits[0]
            f['max_hours'] = limits[1]
        else:
            f['min_hours'] = limits
            f['max_hours'] = limits
    
    # VALIDATE TIME SLOTS - Only use teaching slots (not lunch)
    time_slots = list(TimeSlot.objects.filter(
        slot_type__in=['MORNING', 'AFTERNOON']
    ).values('id', 'day', 'period'))
    
    if not time_slots:
        return {
            'success': False,
            'error': 'No time slots configured. Please initialize time slots first.'
        }
    
    # Verify we have the expected number of teaching slots
    expected_slots = 7 * 5  # 7 periods × 5 days
    if len(time_slots) != expected_slots:
        return {
            'success': False,
            'error': f'Invalid time slot configuration. Expected {expected_slots} teaching slots, found {len(time_slots)}. Please re-initialize time slots.'
        }
    
    # Load faculty preferences
    faculty_preferences = {}
    for f in faculties:
        if f['preferences']:
            faculty_preferences[f['id']] = [p.strip() for p in f['preferences'].split(',')]
    
    # Load faculty history for subject rotation
    faculty_history = defaultdict(list)
    assignments = FacultySubjectAssignment.objects.exclude(
        semester_instance=semester_instance
    ).select_related('subject')
    
    for assignment in assignments:
        faculty_history[assignment.faculty_id].append(assignment.subject.code)
    
    # Initialize and run GA
    ga = GeneticAlgorithm(
        population_size=200,
        generations=1000,
        crossover_rate=0.85,
        mutation_rate=0.15,
        elite_count=10,
        tournament_size=7
    )
    
    ga.load_data(
        classes=classes,
        subjects=subjects,
        faculties=faculties,
        time_slots=time_slots,
        faculty_preferences=faculty_preferences,
        faculty_history=dict(faculty_history)
    )
    
    best_solution, fitness_history = ga.evolve()
    
    # Clear existing entries for this semester instance
    TimetableEntry.objects.filter(
        class_section__semester_id=semester_id,
        semester_instance=semester_instance
    ).delete()
    
    # Save solution to database
    entries_created = []
    for gene in best_solution.genes:
        entry = TimetableEntry.objects.create(
            class_section_id=gene.class_id,
            subject_id=gene.subject_id,
            faculty_id=gene.faculty_id,
            time_slot_id=gene.time_slot_id,
            semester_instance=semester_instance,
            is_lab_session=gene.is_lab,
            assistant_faculty_id=gene.assistant_faculty_id
        )
        entries_created.append(entry)
        
        # Also create faculty-subject assignment for tracking
        FacultySubjectAssignment.objects.get_or_create(
            faculty_id=gene.faculty_id,
            subject_id=gene.subject_id,
            semester_instance=semester_instance,
            class_section_id=gene.class_id,
            defaults={'is_main': True}
        )
        
        if gene.assistant_faculty_id:
            FacultySubjectAssignment.objects.get_or_create(
                faculty_id=gene.assistant_faculty_id,
                subject_id=gene.subject_id,
                semester_instance=semester_instance,
                class_section_id=gene.class_id,
                defaults={'is_main': False}
            )
    
    return {
        'success': True,
        'entries_created': len(entries_created),
        'final_fitness': best_solution.fitness,
        'generations_run': len(fitness_history),
        'fitness_history': fitness_history
    }


def generate_department_timetable(department_id: int, semester_instance: str):
    """
    Generate timetables for ALL semesters and classes within a department.
    
    This ensures faculty conflicts are avoided across the entire department,
    not just within a single semester.
    
    Args:
        department_id: ID of the department to generate timetables for
        semester_instance: e.g., "2024-ODD" or "2024-EVEN"
    
    Returns:
        Dictionary with structured timetable data grouped by semester and class
    """
    from core.models import (
        Department, Semester, ClassSection, Subject, Faculty, TimeSlot,
        FacultySubjectAssignment, TimetableEntry, SystemConfiguration
    )
    
    # Get department info
    department = Department.objects.get(id=department_id)
    
    # Determine which semester numbers to include based on ODD/EVEN
    config = SystemConfiguration.objects.first()
    if config and config.active_semester_type == 'ODD':
        semester_numbers = [1, 3, 5, 7]
    else:
        semester_numbers = [2, 4, 6, 8]
    
    # Get all semesters for this department matching the active type
    semesters = Semester.objects.filter(
        department_id=department_id,
        number__in=semester_numbers
    ).order_by('number')
    
    if not semesters.exists():
        return {
            'success': False,
            'error': f'No {config.active_semester_type} semesters found for {department.code}'
        }
    
    semester_ids = list(semesters.values_list('id', flat=True))
    
    # Get ALL classes across all semesters in this department
    classes = list(ClassSection.objects.filter(
        semester_id__in=semester_ids
    ).values('id', 'name', 'semester_id'))
    
    if not classes:
        return {
            'success': False,
            'error': f'No classes found for {department.code} in {config.active_semester_type} semesters'
        }
    
    # Get ALL subjects across all semesters in this department
    subjects = list(Subject.objects.filter(
        semester_id__in=semester_ids
    ).values('id', 'name', 'code', 'subject_type', 'lecture_hours', 'tutorial_hours', 'practical_hours', 'semester_id'))
    
    # Compute hours_per_week in Python (cannot use @property in .values())
    valid_subjects = []
    for subject in subjects:
        total_hours = subject['lecture_hours'] + subject['tutorial_hours'] + subject['practical_hours']
        subject['hours_per_week'] = total_hours
        
        if total_hours == 0:
            print(f"WARNING: Subject {subject['code']} ({subject['name']}) has zero total hours - skipping from timetable generation")
        else:
            valid_subjects.append(subject)
    
    # Replace subjects list with filtered valid subjects
    if len(valid_subjects) < len(subjects):
        print(f"Skipped {len(subjects) - len(valid_subjects)} subject(s) with zero hours")
    subjects = valid_subjects
    
    if not subjects:
        return {
            'success': False,
            'error': f'No subjects found for {department.code}'
        }
    
    # Get all active faculty for this department
    # Also include faculty from OTHER departments whose preferences match our subjects
    subject_codes = [s['code'] for s in subjects]
    
    # Start with department faculty + unassigned
    dept_faculty_qs = Faculty.objects.filter(
        is_active=True
    ).filter(
        Q(department_id=department_id) | Q(department_id__isnull=True)
    )
    
    # Also find cross-department faculty whose preferences match our subjects
    # This handles BS faculty teaching MAT/PHT/CYT subjects in CS department etc.
    cross_dept_conditions = Q()
    for code in subject_codes:
        cross_dept_conditions |= Q(preferences__contains=code)
    
    cross_dept_faculty_qs = Faculty.objects.filter(
        is_active=True
    ).exclude(
        department_id=department_id
    ).exclude(
        department_id__isnull=True
    ).filter(cross_dept_conditions)
    
    # Combine both querysets (union removes duplicates)
    combined_qs = (dept_faculty_qs | cross_dept_faculty_qs).distinct()
    faculties = list(combined_qs.values('id', 'name', 'designation', 'preferences', 'department_id'))
    
    if not faculties:
        # Fallback to all active faculty
        faculties = list(Faculty.objects.filter(
            is_active=True
        ).values('id', 'name', 'designation', 'preferences', 'department_id'))
    
    # Add min/max hours to faculty data based on designation
    for f in faculties:
        limits = Faculty.WORKLOAD_LIMITS.get(f['designation'], (20, 20))
        if isinstance(limits, tuple):
            f['min_hours'] = limits[0]
            f['max_hours'] = limits[1]
        else:
            f['min_hours'] = limits
            f['max_hours'] = limits
    
    # VALIDATE TIME SLOTS - Only use teaching slots (not lunch)
    time_slots = list(TimeSlot.objects.filter(
        slot_type__in=['MORNING', 'AFTERNOON']
    ).values('id', 'day', 'period'))
    
    if not time_slots:
        return {
            'success': False,
            'error': 'No time slots configured. Please initialize time slots first.'
        }
    
    # Verify we have the expected number of teaching slots
    expected_slots = 7 * 5  # 7 periods × 5 days
    if len(time_slots) != expected_slots:
        return {
            'success': False,
            'error': f'Invalid time slot configuration. Expected {expected_slots} teaching slots, found {len(time_slots)}. Please re-initialize time slots.'
        }
    
    if not time_slots:
        return {
            'success': False,
            'error': 'No time slots configured. Please initialize time slots first.'
        }
    
    # Load faculty preferences
    faculty_preferences = {}
    for f in faculties:
        if f['preferences']:
            faculty_preferences[f['id']] = [p.strip() for p in f['preferences'].split(',')]
    
    # Load faculty history for subject rotation
    faculty_history = defaultdict(list)
    assignments = FacultySubjectAssignment.objects.exclude(
        semester_instance=semester_instance
    ).select_related('subject')
    
    for assignment in assignments:
        faculty_history[assignment.faculty_id].append(assignment.subject.code)
    
    # ── Load pre-booked slots for cross-department faculty ────────────
    # Find which time slots are already booked for our shared faculty
    # in OTHER departments' timetables (already generated).
    faculty_ids = [f['id'] for f in faculties]
    pre_booked_slots = defaultdict(set)
    
    existing_entries = TimetableEntry.objects.filter(
        semester_instance=semester_instance,
        faculty_id__in=faculty_ids
    ).exclude(
        class_section__semester_id__in=semester_ids  # exclude THIS department
    ).values_list('faculty_id', 'time_slot_id')
    
    for fac_id, slot_id in existing_entries:
        pre_booked_slots[fac_id].add(slot_id)
    
    # Also check assistant faculty
    asst_entries = TimetableEntry.objects.filter(
        semester_instance=semester_instance,
        assistant_faculty_id__in=faculty_ids
    ).exclude(
        class_section__semester_id__in=semester_ids
    ).values_list('assistant_faculty_id', 'time_slot_id')
    
    for fac_id, slot_id in asst_entries:
        pre_booked_slots[fac_id].add(slot_id)
    
    if pre_booked_slots:
        print(f"  Cross-department pre-booked: {len(pre_booked_slots)} faculty with existing commitments")
    
    # Initialize and run GA for entire department
    ga = GeneticAlgorithm(
        population_size=200,
        generations=1000,
        crossover_rate=0.85,
        mutation_rate=0.15,
        elite_count=10,
        tournament_size=7
    )
    
    ga.load_data(
        classes=classes,
        subjects=subjects,
        faculties=faculties,
        time_slots=time_slots,
        faculty_preferences=faculty_preferences,
        faculty_history=dict(faculty_history),
        pre_booked_slots=dict(pre_booked_slots),
        department_id=department_id
    )
    
    best_solution, fitness_history = ga.evolve()
    
    # Clear existing entries for ALL semesters in this department for this instance
    TimetableEntry.objects.filter(
        class_section__semester_id__in=semester_ids,
        semester_instance=semester_instance
    ).delete()
    
    # Save solution to database and build structured response
    entries_created = []
    timetables_by_semester = {}
    
    # Build semester info map
    semester_info = {s.id: {'number': s.number, 'name': str(s)} for s in semesters}
    
    # Build class info map
    class_info = {c['id']: c for c in classes}
    
    for gene in best_solution.genes:
        entry = TimetableEntry.objects.create(
            class_section_id=gene.class_id,
            subject_id=gene.subject_id,
            faculty_id=gene.faculty_id,
            time_slot_id=gene.time_slot_id,
            semester_instance=semester_instance,
            is_lab_session=gene.is_lab,
            assistant_faculty_id=gene.assistant_faculty_id
        )
        entries_created.append(entry)
        
        # Build structured response
        class_data = class_info.get(gene.class_id, {})
        sem_id = class_data.get('semester_id')
        
        if sem_id and sem_id in semester_info:
            if sem_id not in timetables_by_semester:
                timetables_by_semester[sem_id] = {
                    'semester_number': semester_info[sem_id]['number'],
                    'semester_name': semester_info[sem_id]['name'],
                    'classes': {}
                }
            
            if gene.class_id not in timetables_by_semester[sem_id]['classes']:
                timetables_by_semester[sem_id]['classes'][gene.class_id] = {
                    'class_name': class_data.get('name', 'Unknown'),
                    'entry_count': 0
                }
            
            timetables_by_semester[sem_id]['classes'][gene.class_id]['entry_count'] += 1
        
        # Create faculty-subject assignment for tracking
        FacultySubjectAssignment.objects.get_or_create(
            faculty_id=gene.faculty_id,
            subject_id=gene.subject_id,
            semester_instance=semester_instance,
            class_section_id=gene.class_id,
            defaults={'is_main': True}
        )
        
        if gene.assistant_faculty_id:
            FacultySubjectAssignment.objects.get_or_create(
                faculty_id=gene.assistant_faculty_id,
                subject_id=gene.subject_id,
                semester_instance=semester_instance,
                class_section_id=gene.class_id,
                defaults={'is_main': False}
            )
    
    return {
        'success': True,
        'department': {
            'id': department.id,
            'name': department.name,
            'code': department.code
        },
        'timetables': timetables_by_semester,
        'total_entries': len(entries_created),
        'classes_count': len(classes),
        'semesters_count': len(semester_ids),
        'final_fitness': best_solution.fitness,
        'generations_run': len(fitness_history)
    }
