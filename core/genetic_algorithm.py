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
        'faculty_clash': -1000,      # Hard: Same faculty in 2 classes at same time
        'class_clash': -1000,        # Hard: Same class has 2 subjects at same time  
        'workload_exceeded': -500,   # Hard: Faculty exceeds max hours
        'lab_continuity': -5000,     # Hard: Lab MUST be in 3 continuous periods
        'lab_timing': -100,          # Soft: Labs should be morning OR afternoon
        'lab_day_clash': -800,       # Hard: Different classes should have labs on different days
        'lab_room_clash': -1500,     # Hard: Same lab subject at same time = room conflict
        'cross_dept_clash': -2000,   # Hard: Faculty already booked in another department
        'two_labs_per_week': -500,   # Hard: Each class must have exactly 2 labs
        'subject_rotation': -50,     # Soft: Penalize same faculty-subject pairs
        'faculty_preference': 100,   # Soft: Bonus for matching preferences
        'workload_balance': -30,     # Soft: Penalize uneven distribution
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
        
        # Mapping for quick lookup
        self.class_subjects = defaultdict(list)  # class_id -> list of subject_ids
        self.subject_info = {}  # subject_id -> {type, hours, etc}
        self.pre_booked_slots = {}  # faculty_id -> set of time_slot_ids (from other departments)
        
    def load_data(self, classes, subjects, faculties, time_slots, 
                  faculty_preferences=None, faculty_history=None,
                  pre_booked_slots=None):
        """Load problem data from Django models
        
        Args:
            pre_booked_slots: Optional dict of faculty_id -> set of time_slot_ids.
                              These are slots already committed in OTHER departments'
                              timetables. The GA will avoid assigning this faculty
                              to these slots.
        """
        self.classes = classes
        self.subjects = subjects
        self.faculties = faculties
        self.time_slots = time_slots
        
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
        
        for f in faculties:
            self.faculty_workload_limits[f['id']] = f['max_hours']
    
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
            
            for subject_id in theory_subjects_for_class:
                hours_needed = self.subject_info[subject_id].get('hours_per_week', 3)
                eligible_faculty = self._get_eligible_faculty_for_subject(subject_id)
                
                if eligible_faculty:
                    # Prefer faculty with fewest global conflicts
                    faculty_id = random.choice(eligible_faculty)
                else:
                    faculty_id = random.choice([f['id'] for f in self.faculties])
                
                # Assign hours across the week
                # Avoid slots where this faculty is pre-booked OR already teaching another class
                faculty_blocked = self.pre_booked_slots.get(faculty_id, set()) | global_faculty_schedule.get(faculty_id, set())
                slots_assigned = 0
                remaining_slots = [s for s in available_slots 
                                   if s not in used_slots and s not in faculty_blocked]
                random.shuffle(remaining_slots)
                
                # If not enough slots excluding blocked, include blocked as fallback
                if len(remaining_slots) < hours_needed:
                    extra = [s for s in available_slots 
                             if s not in used_slots and s in faculty_blocked]
                    remaining_slots.extend(extra)
                
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
                    global_faculty_schedule[faculty_id].add(slot_id)
                    slots_assigned += 1
            
            # ── Fill remaining empty periods ──────────────────────────
            # After scheduling all subjects with their required hours,
            # distribute extra hours among theory subjects to fill all 35 slots.
            # Also include ELECTIVE subjects if available.
            fillable_subjects = [
                s_id for s_id in class_subject_ids 
                if self.subject_info[s_id]['subject_type'] in ('THEORY', 'ELECTIVE')
            ]
            
            if fillable_subjects:
                remaining_slots = [s for s in available_slots if s not in used_slots]
                random.shuffle(remaining_slots)
                
                # Track which faculty was assigned to each subject
                subject_faculty_map = {}
                for gene in genes:
                    if gene.class_id == class_id and not gene.is_lab:
                        subject_faculty_map[gene.subject_id] = gene.faculty_id
                
                # Round-robin fill remaining slots with theory/elective subjects
                # CHECK for faculty clashes before each assignment
                fill_idx = 0
                attempts = 0
                max_attempts = len(remaining_slots) * len(fillable_subjects)
                
                slot_queue = list(remaining_slots)
                while slot_queue and attempts < max_attempts:
                    slot_id = slot_queue[0]
                    subject_id = fillable_subjects[fill_idx % len(fillable_subjects)]
                    faculty_id = subject_faculty_map.get(subject_id)
                    
                    if not faculty_id:
                        eligible = self._get_eligible_faculty_for_subject(subject_id)
                        faculty_id = random.choice(eligible) if eligible else random.choice([f['id'] for f in self.faculties])
                        subject_faculty_map[subject_id] = faculty_id
                    
                    # Check if this faculty is already busy at this slot (in ANY class)
                    if slot_id in global_faculty_schedule.get(faculty_id, set()):
                        # Try next subject instead
                        fill_idx += 1
                        attempts += 1
                        # If we've tried all subjects for this slot, force assign anyway
                        if attempts % len(fillable_subjects) == 0:
                            slot_queue.pop(0)
                            genes.append(Gene(
                                class_id=class_id,
                                subject_id=subject_id,
                                faculty_id=faculty_id,
                                time_slot_id=slot_id,
                                is_lab=False
                            ))
                            used_slots.add(slot_id)
                            global_faculty_schedule[faculty_id].add(slot_id)
                            fill_idx += 1
                        continue
                    
                    slot_queue.pop(0)
                    genes.append(Gene(
                        class_id=class_id,
                        subject_id=subject_id,
                        faculty_id=faculty_id,
                        time_slot_id=slot_id,
                        is_lab=False
                    ))
                    used_slots.add(slot_id)
                    global_faculty_schedule[faculty_id].add(slot_id)
                    fill_idx += 1
                    attempts = 0  # Reset attempt counter on success
        
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
        """Get faculty IDs who can teach a subject based on preferences and capacity"""
        subject = self.subject_info.get(subject_id, {})
        subject_code = subject.get('code', '')
        
        # First priority: faculty who explicitly prefer this subject
        preferred_faculty = []
        for faculty in self.faculties:
            preferences = self.faculty_preferences.get(faculty['id'], [])
            if subject_code in preferences:
                preferred_faculty.append(faculty['id'])
        
        # If any faculty explicitly prefers this subject, use only them
        if preferred_faculty:
            return preferred_faculty
        
        # Otherwise, ALL faculty are eligible (allows even distribution)
        # This ensures subjects without specific preferences get assigned
        # to different faculty members rather than just one
        return [f['id'] for f in self.faculties]
    
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
            
            # Check faculty preference
            preferences = self.faculty_preferences.get(gene.faculty_id, [])
            subject_code = self.subject_info.get(gene.subject_id, {}).get('code', '')
            if subject_code in preferences:
                fitness += self.WEIGHTS['faculty_preference']
            
            # Check cross-department clash (faculty pre-booked in another dept)
            if gene.faculty_id in self.pre_booked_slots:
                if gene.time_slot_id in self.pre_booked_slots[gene.faculty_id]:
                    fitness += self.WEIGHTS['cross_dept_clash']
            if gene.assistant_faculty_id and gene.assistant_faculty_id in self.pre_booked_slots:
                if gene.time_slot_id in self.pre_booked_slots[gene.assistant_faculty_id]:
                    fitness += self.WEIGHTS['cross_dept_clash']
        
        # Check workload limits
        for faculty_id, hours in faculty_hours.items():
            max_hours = self.faculty_workload_limits.get(faculty_id, 20)
            if hours > max_hours:
                fitness += self.WEIGHTS['workload_exceeded'] * (hours - max_hours)
        
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
                        for i, gene in enumerate(lab_genes):
                            gene.time_slot_id = new_lab_slots[i]
        
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
        mutation_type = random.choice(['swap_slot', 'change_faculty', 'swap_subjects'])
        
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
                gene.faculty_id = random.choice(eligible)
        
        elif mutation_type == 'swap_subjects':
            # Swap subjects in the same time slot (different classes)
            gene1 = random.choice(mutated.genes)
            same_slot_genes = [g for g in mutated.genes 
                              if g.time_slot_id == gene1.time_slot_id and g.class_id != gene1.class_id]
            if same_slot_genes:
                gene2 = random.choice(same_slot_genes)
                gene1.faculty_id, gene2.faculty_id = gene2.faculty_id, gene1.faculty_id
        
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
                
                # Repair broken lab blocks after crossover/mutation
                child1 = self._repair_labs(child1)
                child2 = self._repair_labs(child2)
                
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
    
    # Add max_hours to faculty data
    for f in faculties:
        f['max_hours'] = Faculty.WORKLOAD_LIMITS.get(f['designation'], 20)
    
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
        population_size=100,
        generations=500,
        crossover_rate=0.8,
        mutation_rate=0.1,
        elite_count=5,
        tournament_size=5
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
    faculties = list(combined_qs.values('id', 'name', 'designation', 'preferences'))
    
    if not faculties:
        # Fallback to all active faculty
        faculties = list(Faculty.objects.filter(
            is_active=True
        ).values('id', 'name', 'designation', 'preferences'))
    
    # Add max_hours to faculty data
    for f in faculties:
        f['max_hours'] = Faculty.WORKLOAD_LIMITS.get(f['designation'], 20)
    
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
        population_size=100,
        generations=500,
        crossover_rate=0.8,
        mutation_rate=0.1,
        elite_count=5,
        tournament_size=5
    )
    
    ga.load_data(
        classes=classes,
        subjects=subjects,
        faculties=faculties,
        time_slots=time_slots,
        faculty_preferences=faculty_preferences,
        faculty_history=dict(faculty_history),
        pre_booked_slots=dict(pre_booked_slots)
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
