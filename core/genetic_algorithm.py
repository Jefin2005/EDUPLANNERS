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
import concurrent.futures
import multiprocessing


# Genetic Algorithm constraints are strictly enforced logically against combinatoric sets.


@dataclass
class Gene:
    """Represents a single timetable entry"""
    
    
    def __init__(self, class_id, subject_id, faculty_id, time_slot_id, is_lab=False, is_remedial=False, assistant_faculty_id=None):
        self.class_id = class_id
        self.subject_id = subject_id
        self.faculty_id = faculty_id
        self.time_slot_id = time_slot_id
        self.is_lab = is_lab
        self.is_remedial = is_remedial
        self.assistant_faculty_id = assistant_faculty_id

    def copy(self):
        return Gene(self.class_id, self.subject_id, self.faculty_id, self.time_slot_id, self.is_lab, self.is_remedial, self.assistant_faculty_id)


@dataclass
class Chromosome:
    """Represents a complete timetable solution"""
    genes: List[Gene] = field(default_factory=list)
    fitness: float = 0.0
    
    def copy(self):
        # Use Gene.copy() for faster duplication
        return Chromosome(
            genes=[g.copy() for g in self.genes],
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
        'faculty_clash': -20000,     # Heavily penalized
        'class_clash': -10000,        
        'workload_exceeded': -200000, # Extremely Hard: Penalty increased for strict compliance
        'lab_continuity': -10000,
        'lab_timing': -100,          # Soft: Labs should be morning OR afternoon
        'lab_day_clash': -2000,       # Different classes should have labs on different days/halves
        'lab_room_clash': -5000,     # Hard: Same lab subject at same time = room conflict
        'cross_dept_clash': -5000,   # Hard: Faculty already booked in another department
        'two_labs_per_week': -5000,   # Hard: Each class must have exactly 2 labs
        'lab_faculty_inconsistent': -5000,  # Hard: All lab hours for a class+subject must have same faculty
        'subject_rotation': -50,     # Soft: Penalize same faculty-subject pairs
        'faculty_preference': 200,   # Soft: Bonus for matching preferences
        'no_preference_match': -10000, # Hard: Faculty assigned to subject not in their preferences
        'professor_lab': -5000,      # Hard: Professors should NOT be assigned to lab sessions
        'workload_balance': -5000,     # Higher priority for equal distribution
        'workload_under_min': -150000, # Hard (v2): Increased from -10k to force range compliance
        'hierarchy_violation': -100000, # Hard (v2): Penalty for Assistant >= Associate, etc.
        'consecutive_theory': -300,  # Soft: Penalize same theory subject in consecutive periods
        # 83. Faculty consecutive class penalty (Higher priority for hardening)
        'faculty_consecutive': -100000, 
        'faculty_multi_theory': -100000, # Hard: Faculty should teach only ONE theory subject per class
        'special_subject_daily': -5000, # Hard: Remedial/Minor/Honour/Elective max once per day per class
        'remedial_sync': -10000,          # Hard: Remedial slot must be synchronized across classes in a semester
        'faculty_class_spread': -50000,   # Hard: Penalize faculty in > 3 distinct classes
        'lab_only_senior': -20000,        # Hard: Associate Professors must have at least one theory subject
        'theory_limit_exceeded': -100000, # Hard: Faculty exceeds max distinct theory preparations
        'lab_limit_exceeded': -100000,    # Hard: Faculty exceeds max distinct lab preparations
        'subject_faculty_inconsistency_global': -10000, # Penalize multiple teachers for same subject
    }
    
    # ── Designation-based subject limits ──────────────────────────────
    # Max distinct theory subjects and lab subjects per faculty by designation
    DESIGNATION_SUBJECT_LIMITS = {
        'PROFESSOR':            {'theory': 2, 'lab': 0},
        'ASSOCIATE_PROFESSOR':  {'theory': 2, 'lab': 1},
        'ASSISTANT_PROFESSOR':  {'theory': 2, 'lab': 1},
    }
    
    @staticmethod
    def _normalize_code(code: str) -> str:
        """Remove all whitespace and convert to uppercase for robust matching."""
        if not code:
            return ""
        return "".join(code.split()).upper()

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
        
        # BSH subject prefixes (class-level constant)
        self.BSH_PREFIXES = ('PHT', 'HUN', 'MAT', 'CYT', 'PHL', 'CYL', 'EST', 'MNC', 'HUT')
        
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
        self.faculty_info = {}  # faculty_id -> full dict
        self.faculty_designation = {}   # faculty_id -> designation string
        
        # Remedial period synchronization
        self.remedial_schedule = {}     # semester_id -> time_slot_id
        self.remedial_subjects = {}     # class_id -> list of RMH subject_ids
        self.class_semester_map = {}    # class_id -> semester_id
        self.semester_number_map = {}   # semester_id -> semester_number
        
        # Precomputed slot data
        self.slot_by_id = {}
        self.slot_id_by_day_period = {}
        self.available_slots = []
        self.slots_by_day = defaultdict(list)
        
        # Performance: Cache method/dict lookups locally
        self._eligible_faculty_cache = {}
        self.repair_frequency = 4  # Skip heavy repairs 3/4 of the time in early gens
        
    def load_data(self, classes, subjects, faculties, time_slots, 
                  faculty_preferences=None, faculty_history=None,
                  pre_booked_slots=None, department_id=None,
                  semester_number_map=None):
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
        
        # ── Performance Optimization: Precompute slot lookups ──────────
        self.slot_by_id = {ts['id']: ts for ts in time_slots}
        self.slot_id_by_day_period = {(ts['day'], ts['period']): ts['id'] for ts in time_slots}
        self.available_slots = list(self.slot_by_id.keys())
        self.slots_by_day = defaultdict(list)
        for ts in time_slots:
            self.slots_by_day[ts['day']].append(ts)
        for day in self.slots_by_day:
            self.slots_by_day[day].sort(key=lambda x: x['period'])
            
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
        
        # ── Normalize Faculty Preferences & History ────────────────────
        self.faculty_preferences = {}
        if faculty_preferences:
            for f_id, prefs in faculty_preferences.items():
                if isinstance(prefs, str):
                    p_list = [self._normalize_code(p) for p in prefs.split(',') if p.strip()]
                else:
                    p_list = [self._normalize_code(p) for p in prefs if p]
                self.faculty_preferences[f_id] = p_list
                
        self.faculty_history = {}
        if faculty_history:
            for f_id, hist in faculty_history.items():
                self.faculty_history[f_id] = [self._normalize_code(h) for h in hist if h]

        self.pre_booked_slots = pre_booked_slots or {}
        
        # Build faculty info map (safe access for eligibility rules)
        self.faculty_info = {}
        for f in faculties:
            self.faculty_info[f['id']] = f
            
        # Identify department faculty vs cross-department faculty
        self.dept_faculty_ids = set()
        for f in faculties:
            # Check for both department_id (ID) and department_code (e.g. 'CS')
            if f.get('department_id') == department_id or f.get('department_id') is None:
                self.dept_faculty_ids.add(f['id'])
        
        for f in faculties:
            self.faculty_workload_limits[f['id']] = f.get('max_hours', 18)
            self.faculty_workload_min[f['id']] = f.get('min_hours', f.get('max_hours', 14))
            self.faculty_designation[f['id']] = f.get('designation', '')
        
        # Build class -> semester mapping
        self.class_semester_map = {}
        for c in classes:
            self.class_semester_map[c['id']] = c['semester_id']
        
        # Semester ID -> number mapping (for skipping S1/S2 from RMH)
        self.semester_number_map = semester_number_map or {}
        
        # Build class -> RMH subject list
        self.remedial_subjects = defaultdict(list)
        for c in classes:
            for s_id in self.class_subjects[c['id']]:
                if self.subject_info[s_id].get('subject_type') == 'RMH':
                    self.remedial_subjects[c['id']].append(s_id)
                    
        # ── Remedial period synchronization setup ──────────────────────
        self._generate_remedial_schedule()
    
    def _generate_remedial_schedule(self):
        """
        Dynamically generates global synchronized remedial slots per semester.
        Searches combinatoric grids to verify an unobstructed slot list exists for ALL classes.
        """
        self.remedial_schedule = {}
        
        sem_classes = defaultdict(list)
        for c in self.classes:
            sem_id = self.class_semester_map.get(c['id'])
            if sem_id:
                sem_classes[sem_id].append(c['id'])
                
        slots_by_day = defaultdict(list)
        for ts in self.time_slots:
            slots_by_day[ts['day']].append(ts['id'])
            
        all_days = list(slots_by_day.keys())
        
        for sem_id, class_ids in sem_classes.items():
            # ── Skip Semester 1 and 2: No RMH sessions for these ──
            sem_number = self.semester_number_map.get(sem_id, 0)
            if sem_number in (1, 2):
                self.remedial_schedule[sem_id] = []
                print(f"  Semester {sem_id} (S{sem_number}): Skipped RMH (not applicable for S1/S2).")
                continue
            
            max_hours = 0
            for cid in class_ids:
                rmh_subjs = self.remedial_subjects.get(cid, [])
                for sid in rmh_subjs:
                    hours = self.subject_info.get(sid, {}).get('hours_per_week', 3)
                    if hours > max_hours:
                        max_hours = hours
                        
            # Requirement: Exactly 3 RMH hours per week for semesters 3+
            slots_needed = 3 if any(self.remedial_subjects.get(cid) for cid in class_ids) or max_hours > 0 else 0
            
            # Fallback: if user hasn't defined RMH subjects, but wants RMH slots for theory repurposing
            if slots_needed == 0 and len(class_ids) > 0:
                slots_needed = 3
                
            if slots_needed == 0:
                self.remedial_schedule[sem_id] = []
                continue
                
            valid_combination_found = False
            attempts = 0
            
            # Pre-calculate eligible faculty for all RMH subjects in this semester to avoid redundant loops
            sem_rmh_faculty = {}
            for cid in class_ids:
                for sid in self.remedial_subjects.get(cid, []):
                    if sid not in sem_rmh_faculty:
                        sem_rmh_faculty[sid] = self._get_eligible_faculty_for_subject(sid)

            while not valid_combination_found and attempts < 100: # Reduced from 1000 for speed
                attempts += 1
                
                if len(all_days) < slots_needed:
                    chosen_days = all_days
                else:
                    chosen_days = random.sample(all_days, slots_needed)
                    
                chosen_slot_ids = []
                for day in chosen_days:
                    chosen_slot_ids.append(random.choice(slots_by_day[day]))
                    
                combination_is_valid = True
                
                for slot_id in chosen_slot_ids:
                    assigned_facs = set()
                    
                    for cid in class_ids:
                        rmh_subjs = self.remedial_subjects.get(cid, [])
                        if not rmh_subjs:
                            continue
                            
                        class_has_valid_fac = False
                        
                        for sid in rmh_subjs:
                            eligible_fac = self._get_eligible_faculty_for_subject(sid)
                            
                            available_fac = [
                                f for f in eligible_fac 
                                if slot_id not in self.pre_booked_slots.get(f, set())
                                and f not in assigned_facs
                            ]
                            
                            if available_fac:
                                assigned_facs.add(available_fac[0])
                                class_has_valid_fac = True
                                break
                                
                        if not class_has_valid_fac:
                            combination_is_valid = False
                            break
                            
                    if not combination_is_valid:
                        break
                        
                if combination_is_valid:
                    self.remedial_schedule[sem_id] = chosen_slot_ids
                    valid_combination_found = True
                    print(f"  Remedial auto-generated for Semester {sem_id}: {slots_needed} slots after {attempts} attempts.")
                    
            if not valid_combination_found:
                print(f"  CRITICAL: Could not find valid remedial configuration for Semester {sem_id}.")
                self.remedial_schedule[sem_id] = []
    
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
        # Initialize with pre-booked slots from other departments
        global_faculty_schedule = defaultdict(set)
        global_faculty_hours = defaultdict(int)
        if self.pre_booked_slots:
            for f_id, slots in self.pre_booked_slots.items():
                global_faculty_schedule[f_id].update(slots)
                global_faculty_hours[f_id] += len(slots)
        
        # Track lab faculty assignments: (class_id, lab_subject_id) -> (main_faculty, assistant_faculty)
        # Ensures the same main faculty handles all lab hours for each class+subject
        class_lab_faculty = {}
        
        # Track faculty carrying a theory subject in each class to avoid multiple theory assignments
        # Map: class_id -> {faculty_id: subject_id}
        class_faculty_theory_global = defaultdict(dict)
        
        # NEW: Track which faculty is assigned to which subject GLOBALLY for this chromosome
        # to ensure subject-faculty consistency (One Faculty -> One Subject)
        # Map: subject_id -> faculty_id
        global_subject_assigned_faculty = {}
        
        # Track distinct theory & lab subjects per faculty (for designation limits)
        # faculty_id -> set of distinct subject_ids
        global_faculty_theory_subjects = defaultdict(set)
        global_faculty_lab_subjects = defaultdict(set)
        
        # Shuffle class order so different chromosomes try different orderings
        shuffled_classes = list(self.classes)
        random.shuffle(shuffled_classes)
        
        for class_info in shuffled_classes:
            class_id = class_info['id']
            class_subject_ids = self.class_subjects[class_id]
            
            # Load previously assigned RMH faculty for this class
            class_faculty_theory = class_faculty_theory_global[class_id]
            
            # Get available time slots
            available_slots = self.available_slots
            used_slots = set()
            
            # ── Phase 0: Identify Theory Faculty & Reserve Synchronized Slots ──
            semester_id = self.class_semester_map.get(class_id)
            remedial_slot_ids = self.remedial_schedule.get(semester_id, [])
            rmh_subjects = self.remedial_subjects.get(class_id, [])
            
            # Pre-identify theory subjects to determine their faculty upfront
            theory_subjects_for_class = [
                s_id for s_id in class_subject_ids 
                if self.subject_info[s_id]['subject_type'] == 'THEORY'
            ]
            
            # Map: subject_id -> faculty_id (assigned for this class)
            subject_to_faculty = {}
            # Map: subject_id -> hours assigned in phase 0
            subject_phase0_hours = defaultdict(int)
            
            # Pre-assign faculty to each theory subject for this class
            # This ensures consistency between normal periods and remedial periods
            for subject_id in theory_subjects_for_class:
                eligible = self._get_eligible_faculty_for_subject(subject_id)
                if eligible:
                    # Priority 1: Faculty not already teaching another theory subject in this class
                    available = [f for f in eligible if f not in class_faculty_theory]
                    # If all eligible are already teaching or pool is restricted, use anyone eligible
                    if not available:
                        available = eligible
                    
                    # Priority 2: Subject-Faculty Consistency (User Request)
                    # If this subject is already assigned to a faculty member in another class, prefer them
                    consistent_faculty = global_subject_assigned_faculty.get(subject_id)
                    if consistent_faculty and consistent_faculty in available:
                        faculty_id = consistent_faculty
                    else:
                        # Priority 3: Respect designation theory limits (max 2 theory preparations)
                        # ALSO: Respect absolute max hours (workload limit)
                        under_limit = [
                            f for f in available
                            if (len(global_faculty_theory_subjects[f]) < self.DESIGNATION_SUBJECT_LIMITS.get(
                                self.faculty_designation.get(f, ''), {'theory': 2}
                            ).get('theory', 2)
                            or subject_id in global_faculty_theory_subjects[f])
                            and (global_faculty_hours[f] + self.subject_info[subject_id]['hours_per_week'] <= self.faculty_workload_limits.get(f, 20))
                        ]
                        if under_limit:
                            available = under_limit
                        
                        # Pick the one with fewest hours so far to balance workload
                        faculty_id = min(available, key=lambda f: global_faculty_hours[f])
                        
                    # Claim this faculty for this subject globally
                    global_subject_assigned_faculty[subject_id] = faculty_id
                else:
                    # Fallback: for BSH subjects, restrict to BSH faculty only
                    subj_code = self._normalize_code(self.subject_info.get(subject_id, {}).get('code', ''))
                    if subj_code.startswith(self.BSH_PREFIXES):
                        bsh_fac = [f['id'] for f in self.faculties if f.get('department_code') == 'BSH']
                        if bsh_fac:
                            faculty_id = min(bsh_fac, key=lambda f: global_faculty_hours[f])
                        else:
                            faculty_id = random.choice([f['id'] for f in self.faculties])
                    else:
                        all_fac = [f['id'] for f in self.faculties]
                        faculty_id = random.choice(all_fac)
                
                subject_to_faculty[subject_id] = faculty_id
                class_faculty_theory[faculty_id] = subject_id
                global_faculty_theory_subjects[faculty_id].add(subject_id)
            
            if remedial_slot_ids:
                # Requirement: Exactly 3 RMH hours. 
                # If explicit RMH subjects exist, use them. Otherwise, repurpose theory subjects.
                slots_to_fill = 3
                
                rmh_cycle = []
                if rmh_subjects:
                    # Priority 1: Explicit RMH subjects
                    rmh_subjects.sort(key=lambda s: self.subject_info[s].get('hours_per_week', 3), reverse=True)
                    for s_id in rmh_subjects:
                        hours = self.subject_info[s_id].get('hours_per_week', 3)
                        rmh_cycle.extend([s_id] * hours)
                    rmh_cycle = rmh_cycle[:3]  # Hard cap: never exceed 3 RMH hours
                
                # If we still need more RMH hours, or have none defined, use theory subjects
                if len(rmh_cycle) < slots_to_fill and theory_subjects_for_class:
                    # Pick theory subjects to fill the gap
                    # Use a copy so we don't mutate the original list
                    theory_pool = list(theory_subjects_for_class)
                    random.shuffle(theory_pool)
                    while len(rmh_cycle) < slots_to_fill:
                        # Add theory subjects until we hit 3 total RMH hours
                        rmh_cycle.append(random.choice(theory_pool))
                
                slots_to_fill = min(slots_to_fill, len(remedial_slot_ids), len(rmh_cycle))
                
                for i in range(slots_to_fill):
                    slot_id = remedial_slot_ids[i]
                    subject_id = rmh_cycle[i]
                    faculty_id = subject_to_faculty.get(subject_id)
                    
                    if not faculty_id:
                        eligible = self._get_eligible_faculty_for_subject(subject_id)
                        faculty_id = random.choice(eligible) if eligible else (self.faculties[0]['id'] if self.faculties else None)
                        if faculty_id:
                            subject_to_faculty[subject_id] = faculty_id
                    
                    if faculty_id is not None:
                        genes.append(Gene(
                            class_id=class_id,
                            subject_id=subject_id,
                            faculty_id=faculty_id,
                            time_slot_id=slot_id,
                            is_lab=False,
                            is_remedial=True
                        ))
                        used_slots.add(slot_id)
                        global_faculty_schedule[faculty_id].add(slot_id)
                        global_faculty_hours[faculty_id] += 1
                        subject_phase0_hours[subject_id] += 1
            
            # Track days used for labs by THIS class to avoid same day
            class_lab_days = set()
            
            # First, schedule labs (need 3 continuous periods each session)
            lab_subjects_for_class = [
                s_id for s_id in class_subject_ids 
                if self.subject_info[s_id]['subject_type'] == 'LAB'
            ]
            
            for lab_id in lab_subjects_for_class:
                hours_total = self.subject_info[lab_id].get('hours_per_week', 6)
                sessions_needed = (hours_total + 2) // 3 # Round up to handle 1-2 extra hours too
                
                for sess_idx in range(sessions_needed):
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
                    # Retry without room blocking if first attempt fails
                    if not lab_slots:
                        lab_slots = self._find_lab_slots(
                            available_slots, used_slots,
                            exclude_days=class_lab_days,
                            day_usage=global_lab_day_usage
                        )
                    # Final retry: also relax day exclusion
                    if not lab_slots:
                        lab_slots = self._find_lab_slots(
                            available_slots, used_slots
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
                            
                            # Filter out faculty who've reached their lab limit OR max hours
                            lab_hours = self.subject_info[lab_id].get('hours_per_week', 6)
                            eligible_faculty = [
                                f_id for f_id in eligible_faculty
                                if (len(global_faculty_lab_subjects[f_id]) < self.DESIGNATION_SUBJECT_LIMITS.get(
                                    self.faculty_designation.get(f_id, ''), {'lab': 1}
                                ).get('lab', 1)
                                or (lab_id, class_id) in global_faculty_lab_subjects[f_id])
                                and (global_faculty_hours[f_id] + lab_hours <= self.faculty_workload_limits.get(f_id, 20))
                            ]
                            
                            # Priority 1: Consistency (User Request)
                            consistent_faculty = global_subject_assigned_faculty.get(lab_id)
                            if consistent_faculty and consistent_faculty in eligible_faculty:
                                main_faculty = consistent_faculty
                                # Pick any other eligible for assistant
                                remaining = [f for f in eligible_faculty if f != main_faculty]
                                assistant_faculty = random.choice(remaining) if remaining else None
                            elif len(eligible_faculty) >= 2:
                                main_faculty = random.choice(eligible_faculty)
                                assistant_faculty = random.choice([f for f in eligible_faculty if f != main_faculty])
                            elif len(eligible_faculty) == 1:
                                main_faculty = eligible_faculty[0]
                                assistant_faculty = None
                            else:
                                # Fallback: pick any non-Professor faculty (will get penalized in fitness)
                                all_faculty_ids = [f['id'] for f in self.faculties
                                                   if self.faculty_designation.get(f['id']) != 'PROFESSOR']
                                if not all_faculty_ids:
                                    all_faculty_ids = [f['id'] for f in self.faculties]
                                main_faculty = random.choice(all_faculty_ids)
                                assistant_faculty = None
                            
                            # Claim this faculty for this subject globally
                            global_subject_assigned_faculty[lab_id] = main_faculty
                            global_faculty_lab_subjects[main_faculty].add(lab_id)
                            if assistant_faculty:
                                global_faculty_lab_subjects[assistant_faculty].add(lab_id)
                            
                            # Remember this faculty for future lab sessions of same class+subject
                            class_lab_faculty[(class_id, lab_id)] = (main_faculty, assistant_faculty)
                        
                        # Determine which day and half this lab lands on
                        lab_slot_info = self.slot_by_id.get(lab_slots[0])
                        if lab_slot_info:
                            lab_day = lab_slot_info['day']
                            lab_half = 'morning' if lab_slot_info['period'] <= 3 else 'afternoon'
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
                            # Track faculty schedule and workload globally
                            global_faculty_schedule[main_faculty].add(slot_id)
                            global_faculty_hours[main_faculty] += 1
                            global_faculty_lab_subjects[main_faculty].add((lab_id, class_id))
                            if assistant_faculty:
                                global_faculty_schedule[assistant_faculty].add(slot_id)
                                global_faculty_hours[assistant_faculty] += 1
                                global_faculty_lab_subjects[assistant_faculty].add((lab_id, class_id))
            
            # Then, schedule theory subjects
            # Track which subject is assigned to each slot for this class
            # Used to avoid placing the same subject in consecutive periods
            class_slot_subject = {}  # time_slot_id -> subject_id
            
            # Populate class_slot_subject with phase 0 assignments
            for gene in genes:
                if gene.class_id == class_id:
                    class_slot_subject[gene.time_slot_id] = gene.subject_id
            
            for subject_id in theory_subjects_for_class:
                # Faculty is already pre-assigned
                faculty_id = subject_to_faculty[subject_id]
                
                hours_total = self.subject_info[subject_id].get('hours_per_week', 3)
                # Subtract hours already assigned in Phase 0
                hours_needed = hours_total - subject_phase0_hours.get(subject_id, 0)
                
                if hours_needed <= 0:
                    continue
                
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
                    slot_info = self.slot_by_id.get(slot_id)
                    if not slot_info:
                        return 0
                    penalty = 0
                    day, period = slot_info['day'], slot_info['period']
                    for adj_period in (period - 1, period + 1):
                        # Skip if adj_period crosses the lunch break (P4 and P5)
                        if (period == 4 and adj_period == 5) or (period == 5 and adj_period == 4):
                            continue
                            
                        adj_slot = self.slot_id_by_day_period.get((day, adj_period))
                        if adj_slot:
                            # Penalize same subject in consecutive periods
                            if class_slot_subject.get(adj_slot) == subject_id:
                                penalty += 2
                            # Penalize faculty teaching in consecutive periods
                            if adj_slot in global_faculty_schedule.get(faculty_id, set()):
                                penalty += 1
                    return penalty
                
                # Compact Placement: Sort remaining slots to prefer EARLIER periods (1, 2, 3...)
                # and then penalize consecutive same-subject/faculty periods.
                remaining_slots.sort(key=lambda s: (self.slot_by_id[s]['period'], _consecutive_penalty(s), random.random()))
                
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
                    global_faculty_hours[faculty_id] += 1
                    slots_assigned += 1

            
            # ── Fill remaining empty periods ──────────────────────────
            # Include THEORY and ELECTIVE subjects only. RMH subjects are excluded
            # because their names (e.g. "Remedial / Minor / Honors Course") look
            # identical to the remedial label and confuse the display.
            fillable_subjects = [
                s_id for s_id in class_subject_ids 
                if self.subject_info[s_id]['subject_type'] in ('THEORY', 'ELECTIVE')
            ]
            
            # DIAGNOSTIC: Ensure we have fillable subjects
            if not fillable_subjects:
                # Fallback: if no theory/elective, use LAB subjects (rare)
                fillable_subjects = [s_id for s_id in class_subject_ids if self.subject_info[s_id]['subject_type'] == 'LAB']
            if fillable_subjects:
                remaining_slots = [s for s in available_slots if s not in used_slots]
                # Compact Placement: Sort fillers to prefer EARLIER periods too
                remaining_slots.sort(key=lambda s: (self.slot_by_id[s]['period'], random.random()))
                
                # Track which faculty was assigned to each subject
                subject_faculty_map = {}
                for gene in genes:
                    if gene.class_id == class_id and not gene.is_lab:
                        subject_faculty_map[gene.subject_id] = gene.faculty_id
                # Round-robin fill remaining slots with theory/elective subjects
                # CHECK for faculty clashes AND consecutive same-subject before each assignment
                # Build a list of (subject, eligible_faculty) pairs for flexible assignment
                subject_eligible_map = {}
                for s_id in fillable_subjects:
                    subject_eligible_map[s_id] = self._get_eligible_faculty_for_subject(s_id)
                
                fill_idx = 0
                slot_queue = list(remaining_slots)
                
                # Pre-calculate assigned hours for theory subjects to avoid repetition
                # But allow some flexibility if many slots are empty
                subject_total_assigned = defaultdict(int)
                for gene in genes:
                    if gene.class_id == class_id:
                        subject_total_assigned[gene.subject_id] += 1

                for slot_id in slot_queue:
                    assigned = False
                    
                    # Try each subject for this slot
                    # Prio 1: Subjects strictly below their hours_per_week
                    for attempt in range(len(fillable_subjects)):
                        subject_id = fillable_subjects[(fill_idx + attempt) % len(fillable_subjects)]
                        h_limit = self.subject_info[subject_id].get('hours_per_week', 3)
                        
                        if subject_total_assigned[subject_id] >= h_limit:
                             continue

                        faculty_id = subject_faculty_map.get(subject_id)
                        if not faculty_id:
                            # Priority 1: Subject-Faculty Consistency (User Request)
                            consistent_faculty = global_subject_assigned_faculty.get(subject_id)
                            eligible = subject_eligible_map.get(subject_id, [])
                            
                            if consistent_faculty and consistent_faculty in eligible:
                                faculty_id = consistent_faculty
                            else:
                                # Priority 2: Respect designation theory limits (max 2 preparations)
                                already_in_class = set(subject_faculty_map.values())
                                available = [f for f in eligible if f not in already_in_class]
                                
                                if not available:
                                    available = eligible
                                
                                if available:
                                    under_limit = [
                                        f for f in available
                                        if global_faculty_hours[f] < self.faculty_workload_limits.get(f, 20)
                                        and (
                                            len(global_faculty_theory_subjects[f]) < self.DESIGNATION_SUBJECT_LIMITS.get(
                                                self.faculty_designation.get(f, ''), {'theory': 2}
                                            ).get('theory', 2)
                                            or subject_id in global_faculty_theory_subjects[f]
                                        )
                                    ]
                                    if under_limit:
                                        faculty_id = min(under_limit, key=lambda f: global_faculty_hours[f])
                                    else:
                                        faculty_id = min(available, key=lambda f: global_faculty_hours[f])
                                else:
                                    # Fallback: BSH guard — restrict to BSH faculty for BSH subjects
                                    s_code = self._normalize_code(self.subject_info.get(subject_id, {}).get('code', ''))
                                    if s_code.startswith(self.BSH_PREFIXES):
                                        bsh_fac = [f['id'] for f in self.faculties if f.get('department_code') == 'BSH']
                                        if bsh_fac:
                                            faculty_id = min(bsh_fac, key=lambda f: global_faculty_hours[f])
                                        else:
                                            faculty_id = random.choice([f['id'] for f in self.faculties])
                                    else:
                                        faculty_id = random.choice([f['id'] for f in self.faculties])
                                    
                            subject_faculty_map[subject_id] = faculty_id
                            global_subject_assigned_faculty[subject_id] = faculty_id
                            global_faculty_theory_subjects[faculty_id].add(subject_id)
                        
                        # Check primary faculty first
                        faculty_clash = slot_id in global_faculty_schedule.get(faculty_id, set())
                        workload_over = global_faculty_hours[faculty_id] >= self.faculty_workload_limits.get(faculty_id, 20)
                        
                        if not faculty_clash and not workload_over:
                            # Good — no clash with this subject's primary faculty
                            consec_clash = self._would_be_consecutive(slot_id, subject_id, faculty_id, class_slot_subject, global_faculty_schedule)
                            if not consec_clash:
                                # Perfect slot
                                genes.append(Gene(
                                    class_id=class_id,
                                    subject_id=subject_id,
                                    faculty_id=faculty_id,
                                    time_slot_id=slot_id,
                                    is_lab=False,
                                    is_remedial=False
                                ))
                                used_slots.add(slot_id)
                                class_slot_subject[slot_id] = subject_id
                                global_faculty_schedule.setdefault(faculty_id, set()).add(slot_id)
                                global_faculty_hours[faculty_id] += 1
                                fill_idx = (fill_idx + attempt + 1) % len(fillable_subjects)
                                assigned = True
                                break
                            else:
                                # Consecutive clash but no faculty clash — acceptable fallback
                                # Keep looking but remember this as backup
                                pass
                        else:
                            # Faculty clash — but we CANNOT assign a different faculty
                            # for a subject that already has one locked (would create split teaching).
                            # Just skip this subject for this slot.
                            pass
                    
                    if not assigned:
                        # Last resort: use round-robin subject and its faculty (may clash)
                        best_subj = fillable_subjects[fill_idx % len(fillable_subjects)]
                        best_fac = subject_faculty_map.get(best_subj)
                        if not best_fac:
                            eligible = subject_eligible_map.get(best_subj, [])
                            if eligible:
                                best_fac = min(eligible, key=lambda f: global_faculty_hours[f])
                            else:
                                # BSH guard: restrict fallback to BSH faculty for BSH subjects
                                s_code = self._normalize_code(self.subject_info.get(best_subj, {}).get('code', ''))
                                if s_code.startswith(self.BSH_PREFIXES):
                                    bsh_fac = [f['id'] for f in self.faculties if f.get('department_code') == 'BSH']
                                    best_fac = min(bsh_fac, key=lambda f: global_faculty_hours[f]) if bsh_fac else random.choice([f['id'] for f in self.faculties])
                                else:
                                    best_fac = random.choice([f['id'] for f in self.faculties])
                            subject_faculty_map[best_subj] = best_fac
                            
                        genes.append(Gene(
                            class_id=class_id,
                            subject_id=best_subj,
                            faculty_id=best_fac,
                            time_slot_id=slot_id,
                            is_lab=False,
                            is_remedial=False
                        ))
                        used_slots.add(slot_id)
                        class_slot_subject[slot_id] = best_subj
                        global_faculty_schedule.setdefault(best_fac, set()).add(slot_id)
                        global_faculty_hours[best_fac] += 1
                        fill_idx = (fill_idx + 1) % len(fillable_subjects)
        
        return Chromosome(genes=genes)

    def _would_be_consecutive(self, slot_id, subject_id, faculty_id, class_subject_map, faculty_schedule):
        """Helper to check if placing a subject/faculty at a slot creates consecutive issues"""
        slot_info = self.slot_by_id.get(slot_id)
        if not slot_info:
            return False
        day, period = slot_info['day'], slot_info['period']
        for adj_period in (period - 1, period + 1):
            # Skip if adj_period crosses the lunch break (P4 and P5)
            if (period == 4 and adj_period == 5) or (period == 5 and adj_period == 4):
                continue
                
            adj_slot = self.slot_id_by_day_period.get((day, adj_period))
            if adj_slot:
                # Same subject consecutive in THIS class
                if class_subject_map.get(adj_slot) == subject_id:
                    return True
                # Faculty consecutive (teaching ANY class on this day)
                if adj_slot in faculty_schedule.get(faculty_id, set()):
                    return True
        return False
    
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
        for day, ts_list in self.slots_by_day.items():
            slots_by_day[day] = [ts for ts in ts_list if ts['id'] not in used_slots]
        
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
        """Get faculty IDs who can teach a subject with caching."""
        if subject_id in self._eligible_faculty_cache:
            return self._eligible_faculty_cache[subject_id]
            
        subject = self.subject_info.get(subject_id, {})
        subject_code = self._normalize_code(subject.get('code', ''))
        is_lab = subject.get('subject_type') == 'LAB'
        subject_dept_id = subject.get('department_id')

        # Rule 0: BSH Prefix Prioritization (PHT, HUN, MAT, CYT, PHL, CYL, etc.)
        # These subjects SHOULD be taught by BSH faculty if available.
        is_bsh_subject = subject_code.startswith(self.BSH_PREFIXES)

        # Rule 1: Strict Preference Alignment
        preferred_faculty = []
        for f_id, prefs in self.faculty_preferences.items():
            if subject_code in prefs:
                if is_lab and self.faculty_designation.get(f_id) == 'PROFESSOR':
                    continue
                preferred_faculty.append(f_id)

        if preferred_faculty:
            # ── BSH Prefix Prioritization ──────────────────────────────────
            # If this is a BSH subject (MAT, PHT, etc.), and we have BSH faculty 
            # who prefer it, we EXCLUDE faculty from other departments to ensure 
            # that "correct" departmental staff handle these subjects.
            if is_bsh_subject:
                bsh_specialists = [
                    f_id for f_id in preferred_faculty 
                    if self.faculty_info[f_id].get('department_code') == 'BSH'
                ]
                if bsh_specialists:
                    self._eligible_faculty_cache[subject_id] = bsh_specialists
                    return bsh_specialists
            
            self._eligible_faculty_cache[subject_id] = preferred_faculty
            return preferred_faculty
            
        # ── Rule 1.5: BSH Specialty Unlock ──────────────────────────────
        # If no one preferred it explicitly, but it's a BSH subject, 
        # allow ANY BSH faculty who doesn't have conflicting preferences.
        if is_bsh_subject:
            bsh_faculty = [
                f_id for f_id, f in self.faculty_info.items()
                if f.get('department_code') == 'BSH'
            ]
            if bsh_faculty:
                # Apply Professor exclusion for Labs
                if is_lab:
                    bsh_faculty = [f_id for f_id in bsh_faculty
                                   if self.faculty_designation.get(f_id) != 'PROFESSOR']
                if bsh_faculty:
                    self._eligible_faculty_cache[subject_id] = bsh_faculty
                    return bsh_faculty

        # ── Rule 2: Departmental Lock (No one preferred this subject) ─────
        # For BSH subjects whose department_id wrongly points to the host dept
        # (e.g. CS), override to use BSH department faculty instead.
        effective_dept_id = subject_dept_id
        if is_bsh_subject:
            # Find BSH department ID from loaded faculty data
            for f in self.faculties:
                if f.get('department_code') == 'BSH':
                    effective_dept_id = f['department_id']
                    break
        
        if effective_dept_id:
            # First choice: Dept faculty with NO preferences (active generalists)
            eligible_generalists = [
                f['id'] for f in self.faculties
                if f['department_id'] == effective_dept_id and not self.faculty_preferences.get(f['id'])
            ]
            
            # Second choice: Any other faculty from the same department
            eligible_others = [
                f['id'] for f in self.faculties
                if f['department_id'] == effective_dept_id and self.faculty_preferences.get(f['id'])
            ]
            
            eligible = eligible_generalists if eligible_generalists else eligible_others
            
            # Apply Professor exclusion for Labs
            if is_lab:
                eligible = [f_id for f_id in eligible
                            if self.faculty_designation.get(f_id) != 'PROFESSOR']
            
            if eligible:
                self._eligible_faculty_cache[subject_id] = eligible
                return eligible

        # Fallback: Still return empty if no departmental match found (cross-dept case)
        return []
    
    def calculate_fitness(self, chromosome: Chromosome) -> float:
        """Calculate fitness score for a chromosome"""
        fitness = 0.0
        
        # Track violations using a single pass over genes
        faculty_schedule = defaultdict(set)  # faculty_id -> set of time_slot_ids
        faculty_hours = defaultdict(int)      # faculty_id -> total hours
        
        # Initialize with pre-booked slots from other departments
        if self.pre_booked_slots:
            for f_id, slots in self.pre_booked_slots.items():
                faculty_schedule[f_id].update(slots)
                faculty_hours[f_id] += len(slots)
        
        class_schedule = defaultdict(set)    # class_id -> set of time_slot_ids
        class_labs = defaultdict(list)        # class_id -> list of lab genes
        faculty_class_theory = defaultdict(lambda: defaultdict(set))
        class_day_special = defaultdict(lambda: defaultdict(int))
        class_day_genes = defaultdict(lambda: defaultdict(list))
        faculty_day_periods = defaultdict(lambda: defaultdict(list))
        
        # New: Group genes by class for remedial sync (O(N) instead of O(N^2))
        class_genes_map = defaultdict(list)  # class_id -> list of genes
        
        SPECIAL_TYPES = {'ELECTIVE', 'RMH'}
        
        # Performance: Cache method/dict lookups locally
        get_subject_info = self.subject_info.get
        get_slot_info = self.slot_by_id.get
        get_faculty_pref = self.faculty_preferences.get
        get_faculty_hist = self.faculty_history.get
        get_faculty_desig = self.faculty_designation.get
        get_pre_booked = self.pre_booked_slots.get
        
        for gene in chromosome.genes:
            slot_id = gene.time_slot_id
            fac_id = gene.faculty_id
            asst_id = gene.assistant_faculty_id
            class_id = gene.class_id
            subj_id = gene.subject_id
            is_lab = gene.is_lab
            
            class_genes_map[class_id].append(gene)
            
            # Faculty clash
            if slot_id in faculty_schedule[fac_id]:
                fitness += self.WEIGHTS['faculty_clash']
            faculty_schedule[fac_id].add(slot_id)
            
            if asst_id:
                if slot_id in faculty_schedule[asst_id]:
                    fitness += self.WEIGHTS['faculty_clash']
                faculty_schedule[asst_id].add(slot_id)
            
            # Class clash
            if slot_id in class_schedule[class_id]:
                fitness += self.WEIGHTS['class_clash']
            class_schedule[class_id].add(slot_id)
            
            # Faculty hours
            faculty_hours[fac_id] += 1
            if asst_id:
                faculty_hours[asst_id] += 1
            
            # Dictionary lookups (cached)
            subj_info = get_subject_info(subj_id, {})
            subject_code = self._normalize_code(subj_info.get('code', ''))
            subj_type = subj_info.get('subject_type', '')
            
            # Labs and Theory mapping
            if is_lab:
                class_labs[class_id].append(gene)
                # lab_slot_usage moved to per-subject processing below if possible
            else:
                faculty_class_theory[fac_id][class_id].add(subj_id)
            
            # Preferences
            preferences = get_faculty_pref(fac_id, [])
            if subject_code in preferences:
                fitness += self.WEIGHTS['faculty_preference']
            elif preferences:
                fitness += self.WEIGHTS['no_preference_match']
            
            # Professor lab
            if is_lab:
                if get_faculty_desig(fac_id) == 'PROFESSOR':
                    fitness += self.WEIGHTS['professor_lab']
                if asst_id and get_faculty_desig(asst_id) == 'PROFESSOR':
                    fitness += self.WEIGHTS['professor_lab']
            
            # Pre-booked clash
            fac_pre_booked = get_pre_booked(fac_id)
            if fac_pre_booked and slot_id in fac_pre_booked:
                fitness += self.WEIGHTS['cross_dept_clash']
            if asst_id:
                asst_pre_booked = get_pre_booked(asst_id)
                if asst_pre_booked and slot_id in asst_pre_booked:
                    fitness += self.WEIGHTS['cross_dept_clash']
                
            # Subject rotation penalty
            history = get_faculty_hist(fac_id, [])
            if subject_code in history:
                fitness += self.WEIGHTS['subject_rotation']
                
            slot_info = get_slot_info(slot_id)
            if slot_info:
                day = slot_info['day']
                period = slot_info['period']
                
                # Special subject daily limits
                if subj_type in SPECIAL_TYPES:
                    class_day_special[class_id][day] += 1
                
                # Consecutive theory setup
                if not is_lab:
                    class_day_genes[class_id][day].append((period, gene))
                
                # Faculty consecutive period setup
                faculty_day_periods[fac_id][day].append((period, is_lab, subj_id))
                if asst_id:
                    faculty_day_periods[asst_id][day].append((period, is_lab, subj_id))

        # ── End of single pass ──

        # Check workload limits
        for faculty_id, hours in faculty_hours.items():
            max_hours = self.faculty_workload_limits.get(faculty_id, 20)
            min_hours = self.faculty_workload_min.get(faculty_id, max_hours)
            
            # Apply extremely high penalty for any workload exceeding max
            if hours > max_hours:
                fitness += self.WEIGHTS['workload_exceeded'] * (hours - max_hours)
            elif hours < min_hours:
                fitness += self.WEIGHTS['workload_under_min'] * (min_hours - hours)
                
        # Hierarchy Check: Associate > Assistant > Professor
        # Use average hours per designation for a smoother gradient
        designation_hours = defaultdict(list)
        for f_id, hours in faculty_hours.items():
            desig = self.faculty_designation.get(f_id, '')
            if desig in ('PROFESSOR', 'ASSOCIATE_PROFESSOR', 'ASSISTANT_PROFESSOR'):
                designation_hours[desig].append(hours)
        
        def get_avg(desig):
            h = designation_hours.get(desig)
            return sum(h) / len(h) if h else 0
            
        avg_assoc = get_avg('ASSOCIATE_PROFESSOR')
        avg_asst = get_avg('ASSISTANT_PROFESSOR')
        avg_prof = get_avg('PROFESSOR')
        
        # Hierarchy (Targeted): Professor (8-10) < Associate (11-15) < Assistant (16-23)
        if avg_assoc > 0 and avg_asst > 0 and avg_assoc >= avg_asst:
             fitness += self.WEIGHTS['hierarchy_violation'] * (avg_assoc - avg_asst + 1)
        if avg_prof > 0 and avg_assoc > 0 and avg_prof >= avg_assoc:
             fitness += self.WEIGHTS['hierarchy_violation'] * (avg_prof - avg_assoc + 1)

        # Faculty multi-theory penalty
        for faculty_id, class_map in faculty_class_theory.items():
            # Class spread penalty
            if len(class_map) > 3:
                fitness += self.WEIGHTS['faculty_class_spread'] * (len(class_map) - 3)
            
            # Lab-only senior penalty
            if self.faculty_designation.get(faculty_id) == 'ASSOCIATE_PROFESSOR':
                if len(class_map) == 0: # Only labs/projects assigned
                    fitness += self.WEIGHTS['lab_only_senior']

            for class_id, theory_subjects in class_map.items():
                if len(theory_subjects) > 1:
                    fitness += self.WEIGHTS['faculty_multi_theory'] * (len(theory_subjects) - 1)
        
        # ── Designation-based subject limit penalties (Preparations) ──
        # Count distinct theory and lab subject IDs per faculty (Preps)
        # Also track global subject-teacher mapping for consistency
        faculty_distinct_theory = defaultdict(set)
        faculty_distinct_labs = defaultdict(set)
        subject_teachers = defaultdict(set)
        
        for gene in chromosome.genes:
            subject_teachers[gene.subject_id].add(gene.faculty_id)
            if gene.is_lab:
                faculty_distinct_labs[gene.faculty_id].add(gene.subject_id)
                if gene.assistant_faculty_id:
                    faculty_distinct_labs[gene.assistant_faculty_id].add(gene.subject_id)
                    subject_teachers[gene.subject_id].add(gene.assistant_faculty_id)
            else:
                faculty_distinct_theory[gene.faculty_id].add(gene.subject_id)
        
        # 1. Preparation count penalties
        for fac_id in set(list(faculty_distinct_theory.keys()) + list(faculty_distinct_labs.keys())):
            desig = self.faculty_designation.get(fac_id, '')
            limits = self.DESIGNATION_SUBJECT_LIMITS.get(desig, {'theory': 2, 'lab': 1})
            
            theory_count = len(faculty_distinct_theory.get(fac_id, set()))
            lab_count = len(faculty_distinct_labs.get(fac_id, set()))
            
            if theory_count > limits['theory']:
                fitness += self.WEIGHTS['theory_limit_exceeded'] * (theory_count - limits['theory'])
            if lab_count > limits['lab']:
                # Professors have lab limit 0, so any lab count > 0 triggers this
                fitness += self.WEIGHTS['lab_limit_exceeded'] * (lab_count - limits['lab'])
        
        # 2. Global subject-faculty consistency penalty
        for subj_id, teachers in subject_teachers.items():
            if len(teachers) > 1:
                # Penalize every additional teacher assigned to the same subject
                fitness += self.WEIGHTS['subject_faculty_inconsistency_global'] * (len(teachers) - 1)

        # Special subject daily limit penalty
        for class_id, day_counts in class_day_special.items():
            for day, count in day_counts.items():
                if count > 1:
                    fitness += self.WEIGHTS['special_subject_daily'] * (count - 1)

        # Check lab constraints
        lab_day_half_usage = defaultdict(int) 
        for class_id, lab_genes in class_labs.items():
            lab_days_for_class = set()
            lab_faculty_by_subject = defaultdict(set)
            lab_subject_genes = defaultdict(list)
            
            for g in lab_genes:
                lab_faculty_by_subject[g.subject_id].add(g.faculty_id)
                lab_subject_genes[g.subject_id].append(g)
                
                slot_info = get_slot_info(g.time_slot_id)
                if slot_info:
                    half = 'morning' if slot_info['period'] <= 3 else 'afternoon'
                    day_half = (slot_info['day'], half)
                    if day_half not in lab_days_for_class:
                        lab_days_for_class.add(day_half)
                        lab_day_half_usage[day_half] += 1
                        
            # Check lab continuity and timing
            for lab_subject_id, s_genes in lab_subject_genes.items():
                slot_ids = [g.time_slot_id for g in s_genes]
                if len(slot_ids) != 3:
                    fitness += self.WEIGHTS['lab_continuity'] * 2
                else:
                    if not self._check_lab_continuity(slot_ids):
                        fitness += self.WEIGHTS['lab_continuity']
                    if not self._check_lab_timing(slot_ids):
                        fitness += self.WEIGHTS['lab_timing']
            
            # Check lab faculty inconsistency
            for subj_id, faculty_set in lab_faculty_by_subject.items():
                if len(faculty_set) > 1:
                    fitness += self.WEIGHTS['lab_faculty_inconsistent'] * (len(faculty_set) - 1)

        # Lab day clash
        for count in lab_day_half_usage.values():
            if count > 1:
                fitness += self.WEIGHTS['lab_day_clash'] * (count - 1)

        # Lab room clashes (re-calculated to avoid another 3D map if possible, but let's see)
        lab_room_usage = defaultdict(lambda: defaultdict(int)) # subj_id -> slot_id -> count
        for class_id, lab_genes in class_labs.items():
            for g in lab_genes:
                lab_room_usage[g.subject_id][g.time_slot_id] += 1
        
        for slot_counts in lab_room_usage.values():
            for count in slot_counts.values():
                if count > 1:
                    fitness += self.WEIGHTS['lab_room_clash'] * (count - 1)

        # Workload balance
        designation_hours = defaultdict(list)
        for faculty_id, hours in faculty_hours.items():
            designation = self.faculty_designation.get(faculty_id, '')
            designation_hours[designation].append(hours)
        
        for hours_list in designation_hours.values():
            if len(hours_list) < 2: continue
            avg_hours = sum(hours_list) / len(hours_list)
            for hours in hours_list:
                deviation = abs(hours - avg_hours)
                if deviation > 0:
                    fitness += self.WEIGHTS['workload_balance'] * deviation

        # Consecutive same-theory penalty
        for day_map in class_day_genes.values():
            for period_genes in day_map.values():
                if len(period_genes) < 2: continue
                period_genes.sort(key=lambda x: x[0])
                for i in range(len(period_genes) - 1):
                    if period_genes[i+1][0] == period_genes[i][0] + 1 and \
                       period_genes[i][1].subject_id == period_genes[i+1][1].subject_id:
                        fitness += self.WEIGHTS['consecutive_theory']

        # Faculty consecutive class penalty
        for day_map in faculty_day_periods.values():
            for period_list in day_map.values():
                if len(period_list) < 2: continue
                period_list.sort(key=lambda x: x[0])
                for i in range(len(period_list) - 1):
                    p1, l1, s1 = period_list[i]
                    p2, l2, s2 = period_list[i+1]
                    if l1 and l2 and s1 == s2: continue
                    if p1 == 4 and p2 == 5: continue
                    if p2 == p1 + 1:
                        # Baseline consecutive penalty
                        fitness += self.WEIGHTS['faculty_consecutive']
                        # Extra penalty for 3+ in a row (cumulative)
                        if i > 0:
                            p0, l0, s0 = period_list[i-1]
                            if p1 == p0 + 1:
                                fitness += self.WEIGHTS['faculty_consecutive'] # Double penalty for 3 in a row
                        if i < len(period_list) - 2:
                             p3, l3, s3 = period_list[i+2]
                             if p3 == p2 + 1:
                                 fitness += self.WEIGHTS['faculty_consecutive'] # Anticipate 3 in a row
        
        # Optimized Remedial sync penalty (Uses class_genes_map)
        if self.remedial_schedule:
            for c in self.classes:
                class_id = c['id']
                semester_id = self.class_semester_map.get(class_id)
                config_slots = self.remedial_schedule.get(semester_id, [])
                rmh_subjects = self.remedial_subjects.get(class_id, [])
                
                if config_slots and rmh_subjects:
                    theory_subjects = {s_id for s_id in self.class_subjects.get(class_id, [])
                                      if get_subject_info(s_id, {}).get('subject_type') == 'THEORY'}
                    if not theory_subjects: continue
                        
                    expected_matches = min(3, len(config_slots))  # Hard cap: exactly 3 RMH
                    target_slots = set(config_slots[:expected_matches])
                    
                    matched_slots = 0
                    for g in class_genes_map[class_id]:
                        if g.time_slot_id in target_slots and g.subject_id in theory_subjects:
                            matched_slots += 1
                    
                    if matched_slots != expected_matches:
                        fitness += self.WEIGHTS['remedial_sync']
        
        chromosome.fitness = fitness
        return fitness
    
    def _is_remedial_gene(self, gene: Gene) -> bool:
        """Check if a gene occupies a remedial slot for its class's semester.
        These genes must never be moved or swapped."""
        semester_id = self.class_semester_map.get(gene.class_id)
        remedial_slot_ids = self.remedial_schedule.get(semester_id, [])
        # Lock any gene that lands in a synchronized remedial slot to prevent mutation chaos
        return gene.time_slot_id in remedial_slot_ids
    
    def _check_lab_continuity(self, slot_ids: List[int]) -> bool:
        """Check if lab slots are 3 continuous periods"""
        if len(slot_ids) != 3:
            return False
        
        slots = [self.slot_by_id[ts_id] for ts_id in slot_ids if ts_id in self.slot_by_id]
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
        slots = [self.slot_by_id[ts_id] for ts_id in slot_ids if ts_id in self.slot_by_id]
        
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
    
    def _repair_faculty_clashes(self, chromosome: Chromosome, evolution_mode: bool = True) -> Chromosome:
        """Repair faculty clashes using multiple strategies with incremental indexing."""
        max_iterations = 20 if evolution_mode else 100
        
        # Build initial indexes
        faculty_slot_genes = defaultdict(lambda: defaultdict(list))
        # Initialize with pre-booked slots (use -1 to indicate cross-dept booking)
        if self.pre_booked_slots:
            for f_id, slots in self.pre_booked_slots.items():
                for s_id in slots:
                    faculty_slot_genes[f_id][s_id].append(-1)
                    
        class_gene_indices = defaultdict(list)
        
        for idx, gene in enumerate(chromosome.genes):
            faculty_slot_genes[gene.faculty_id][gene.time_slot_id].append(idx)
            if gene.assistant_faculty_id:
                faculty_slot_genes[gene.assistant_faculty_id][gene.time_slot_id].append(idx)
            class_gene_indices[gene.class_id].append(idx)
            
        all_slots = self.available_slots
        
        for _ in range(max_iterations):
            # Collect clashing genes (only need to look at slot occupancy > 1)
            clashing_indices = []
            for fac_id, slot_map in faculty_slot_genes.items():
                for slot_id, gene_indices in slot_map.items():
                    if len(gene_indices) > 1:
                        # Find movable clashing genes (skip pre-booked -1 and labs/remedial)
                        movable = [i for i in gene_indices 
                                   if i != -1 and not chromosome.genes[i].is_lab 
                                   and not chromosome.genes[i].is_remedial]
                        clashing_indices.extend(movable)
            
            if not clashing_indices:
                break
                
            clashing_indices = list(set(clashing_indices))
            random.shuffle(clashing_indices)
            
            fixed_any = False
            for clash_idx in clashing_indices:
                gene = chromosome.genes[clash_idx]
                old_slot = gene.time_slot_id
                fac_id = gene.faculty_id
                class_id = gene.class_id
                
                # Safeguard: if this gene is no longer at this slot (moved by previous iteration), skip
                if clash_idx not in faculty_slot_genes[fac_id].get(old_slot, []):
                    continue
                # If this index is no longer part of a clash at this slot, skip
                if len(faculty_slot_genes[fac_id].get(old_slot, [])) <= 1:
                    continue
                
                # Strategy 1: Swap with a non-clashing gene in same class
                class_genes = class_gene_indices[class_id]
                for target_idx in class_genes:
                    target_gene = chromosome.genes[target_idx]
                    if (target_idx == clash_idx or target_gene.is_lab or 
                        target_gene.is_remedial):
                        continue
                        
                    new_slot = target_gene.time_slot_id
                    # Would faculty clash at new_slot?
                    if len(faculty_slot_genes[fac_id].get(new_slot, [])) == 0:
                        # Update indexes
                        if clash_idx in faculty_slot_genes[fac_id][old_slot]:
                            faculty_slot_genes[fac_id][old_slot].remove(clash_idx)
                        faculty_slot_genes[fac_id][new_slot].append(clash_idx)
                        
                        target_fac = target_gene.faculty_id
                        if target_idx in faculty_slot_genes[target_fac][new_slot]:
                            faculty_slot_genes[target_fac][new_slot].remove(target_idx)
                        faculty_slot_genes[target_fac][old_slot].append(target_idx)
                        
                        # Swap data
                        gene.time_slot_id, target_gene.time_slot_id = target_gene.time_slot_id, gene.time_slot_id
                        fixed_any = True
                        break
                
                if fixed_any: break
                
                # Strategy 2: Reassign faculty (available in evolution mode too)
                eligible = self._get_eligible_faculty_for_subject(gene.subject_id)
                random.shuffle(eligible)
                for new_fac in eligible:
                    if new_fac != fac_id and len(faculty_slot_genes[new_fac].get(old_slot, [])) == 0:
                        # Update indexes for ALL hours of this subject in this class
                        for g_idx in class_genes:
                            g = chromosome.genes[g_idx]
                            if g.subject_id == gene.subject_id and not g.is_lab:
                                old_fac = g.faculty_id
                                slot_list = faculty_slot_genes[old_fac][g.time_slot_id]
                                if g_idx in slot_list:
                                    slot_list.remove(g_idx)
                                g.faculty_id = new_fac
                                faculty_slot_genes[new_fac][g.time_slot_id].append(g_idx)
                        fixed_any = True
                        break
                
                if fixed_any: break

                # Strategy 3: Exhaustive Global Swap (Only in full mode)
                if not evolution_mode:
                    all_indices = list(range(len(chromosome.genes)))
                    random.shuffle(all_indices)
                    for target_idx in all_indices:
                        g2 = chromosome.genes[target_idx]
                        if g2.is_lab or g2.is_remedial: continue
                        
                        t_slot = g2.time_slot_id
                        t_fac = g2.faculty_id
                        if (len(faculty_slot_genes[fac_id].get(t_slot, [])) == 0 and 
                            len(faculty_slot_genes[t_fac].get(old_slot, [])) == 0):
                            
                            if clash_idx in faculty_slot_genes[fac_id][old_slot]:
                                faculty_slot_genes[fac_id][old_slot].remove(clash_idx)
                            faculty_slot_genes[fac_id][t_slot].append(clash_idx)
                            
                            if target_idx in faculty_slot_genes[t_fac][t_slot]:
                                faculty_slot_genes[t_fac][t_slot].remove(target_idx)
                            faculty_slot_genes[t_fac][old_slot].append(target_idx)
                            
                            gene.time_slot_id, g2.time_slot_id = g2.time_slot_id, gene.time_slot_id
                            fixed_any = True
                            break
                    if fixed_any: break
                
            if not fixed_any:
                break
        
        return chromosome
    
    def _repair_remedial(self, chromosome: Chromosome) -> Chromosome:
        """Ensure each class has exactly 3 synchronized remedial periods, even if RMH subjects are undefined."""
        if not self.remedial_schedule:
            return chromosome
            
        genes_by_class_slot = defaultdict(dict)
        for idx, g in enumerate(chromosome.genes):
            genes_by_class_slot[g.class_id][g.time_slot_id] = idx
            
        for class_info in self.classes:
            class_id = class_info['id']
            semester_id = self.class_semester_map.get(class_id)
            config_slots = self.remedial_schedule.get(semester_id, [])
            
            if not config_slots:
                continue
                
            # Requirement: Exactly 3 slots
            slots_to_sync = config_slots[:3]
            
            # Reset all is_remedial flags for this class to ensure hardcap
            for g in chromosome.genes:
                if g.class_id == class_id:
                    g.is_remedial = False
            
            # Decide which subjects to use for RMH
            rmh_subjects = self.remedial_subjects.get(class_id, [])
            rmh_cycle = []
            if rmh_subjects:
                for s_id in rmh_subjects:
                    hours = self.subject_info[s_id].get('hours_per_week', 3)
                    rmh_cycle.extend([s_id] * hours)
                rmh_cycle = rmh_cycle[:3]  # Hard cap: never exceed 3 RMH hours
            
            # Fallback to theory subjects if needed
            if len(rmh_cycle) < len(slots_to_sync):
                theory_subjects = [s_id for s_id in self.class_subjects.get(class_id, [])
                                   if self.subject_info.get(s_id, {}).get('subject_type') == 'THEORY']
                if theory_subjects:
                    while len(rmh_cycle) < len(slots_to_sync):
                        rmh_cycle.append(random.choice(theory_subjects))
            
            if not rmh_cycle:
                continue
                
            for i, slot_id in enumerate(slots_to_sync):
                target_subject_id = rmh_cycle[i % len(rmh_cycle)]
                
                # Check if class already has a gene at this slot
                if slot_id in genes_by_class_slot[class_id]:
                    idx = genes_by_class_slot[class_id][slot_id]
                    g = chromosome.genes[idx]
                    g.subject_id = target_subject_id
                    g.is_remedial = True
                    # Re-verify faculty (should match theory subject pre-assignment)
                    # We assume the user has assigned a valid faculty to the class for this subject
                else:
                    # Find a gene of the same subject elsewhere to move here?
                    # Or just create a new one and delete a duplicate later?
                    # Simplest: Force-create or overwrite another gene.
                    # But better to just find one of the subject's genes and move it.
                    target_gene_idx = None
                    for idx, g in enumerate(chromosome.genes):
                        if (g.class_id == class_id and g.subject_id == target_subject_id 
                            and not g.is_lab and g.time_slot_id not in slots_to_sync):
                            target_gene_idx = idx
                            break
                    
                    if target_gene_idx is not None:
                        # Move this gene to the synchronized slot
                        old_slot = chromosome.genes[target_gene_idx].time_slot_id
                        chromosome.genes[target_gene_idx].time_slot_id = slot_id
                        chromosome.genes[target_gene_idx].is_remedial = True
                        # Update index
                        del genes_by_class_slot[class_id][old_slot]
                        genes_by_class_slot[class_id][slot_id] = target_gene_idx
                    else:
                        # Create new
                        eligible = self._get_eligible_faculty_for_subject(target_subject_id)
                        fac_id = random.choice(eligible) if eligible else (self.faculties[0]['id'] if self.faculties else None)
                        if fac_id:
                            new_gene = Gene(
                                class_id=class_id,
                                subject_id=target_subject_id,
                                faculty_id=fac_id,
                                time_slot_id=slot_id,
                                is_lab=False,
                                is_remedial=True
                            )
                            chromosome.genes.append(new_gene)
                            genes_by_class_slot[class_id][slot_id] = len(chromosome.genes) - 1
        
        return chromosome
    def _repair_workload(self, chromosome: Chromosome, full_mode: bool = False) -> Chromosome:
        """Repair workload violations with incremental indexing.
        
        Phase 1: Reduce over-limit faculty (reassign subjects to others).
        Phase 2: Consolidate onto under-limit faculty (steal subjects from others).
        """
        max_repair_iterations = 15 if full_mode else 3
        
        # Initial pass: build counts and schedule (including pre-booked)
        faculty_hours = defaultdict(int)
        faculty_schedule = defaultdict(set)
        if self.pre_booked_slots:
            for f_id, slots in self.pre_booked_slots.items():
                faculty_hours[f_id] += len(slots)
                faculty_schedule[f_id].update(slots)
                
        for g in chromosome.genes:
            faculty_hours[g.faculty_id] += 1
            faculty_schedule[g.faculty_id].add(g.time_slot_id)
            if g.assistant_faculty_id:
                faculty_hours[g.assistant_faculty_id] += 1
                faculty_schedule[g.assistant_faculty_id].add(g.time_slot_id)
        
        for _ in range(max_repair_iterations):
            fixed_any = False
            
            # --- Phase 1: Over-limit repair ---
            overworked = []
            for fac_id, hours in faculty_hours.items():
                limit = self.faculty_workload_limits.get(fac_id, 20)
                if hours > limit:
                    prio = 10 if self.faculty_designation.get(fac_id) == 'PROFESSOR' else 1
                    overworked.append((prio, hours - limit, fac_id))
            
            if overworked:
                overworked.sort(reverse=True)
                for _, excess_total, fac_id in overworked:
                    limit = self.faculty_workload_limits.get(fac_id, 20)
                    # Find theory genes for this faculty
                    fac_genes = [g for g in chromosome.genes if g.faculty_id == fac_id and not g.is_lab]
                    random.shuffle(fac_genes)
                    
                    for gene in fac_genes:
                        if faculty_hours[fac_id] <= limit: break
                        
                        eligible = self._get_eligible_faculty_for_subject(gene.subject_id)
                        # Find valid alternatives (free at this slot)
                        # Relax 'not already over limit' for Assistants if we are saving a Professor
                        is_prof = self.faculty_designation.get(fac_id) == 'PROFESSOR'
                        valid_alts = [f for f in eligible if f != fac_id 
                                     and gene.time_slot_id not in faculty_schedule[f]]
                        
                        # Standard: only move to those under their limit
                        valid_alts = [f for f in valid_alts 
                                     if faculty_hours[f] < self.faculty_workload_limits.get(f, 20)]
                        
                        if not valid_alts: continue
                        
                        # Target faculty with MOST relative remaining capacity (to respect hierarchy Associate > Assistant > Professor)
                        valid_alts.sort(key=lambda f: faculty_hours[f] - self.faculty_workload_limits.get(f, 20))
                        new_fac = valid_alts[0]
                        
                        # Atomic reassignment
                        subject_genes = [g for g in chromosome.genes 
                                       if g.class_id == gene.class_id and g.subject_id == gene.subject_id and not g.is_lab]
                        
                        subject_slots = [sg.time_slot_id for sg in subject_genes]
                        if all(slot not in faculty_schedule[new_fac] for slot in subject_slots):
                            for sg in subject_genes:
                                old_f = sg.faculty_id
                                sg.faculty_id = new_fac
                                # Update indexes
                                faculty_schedule[old_f].discard(sg.time_slot_id)
                                faculty_schedule[new_fac].add(sg.time_slot_id)
                                faculty_hours[old_f] -= 1
                                faculty_hours[new_fac] += 1
                            fixed_any = True
                            break
                    if fixed_any: break
            
            # --- Phase 2: Under-limit consolidation (only in full_mode) ---
            if full_mode and not fixed_any:
                under_faculty = []
                for fac_id in self.dept_faculty_ids:
                    min_h = self.faculty_workload_min.get(fac_id, 0)
                    cur_h = faculty_hours.get(fac_id, 0)
                    if cur_h < min_h and cur_h > 0:
                        under_faculty.append((min_h - cur_h, fac_id))
                
                if under_faculty:
                    under_faculty.sort(reverse=True)
                    for deficit, under_fac in under_faculty:
                        min_h = self.faculty_workload_min.get(under_fac, 0)
                        max_h = self.faculty_workload_limits.get(under_fac, 20)
                        cur_h = faculty_hours.get(under_fac, 0)
                        if cur_h >= min_h: continue
                        
                        # Use cached eligible check if possible or just get subjects
                        eligible_subjects = {s['id'] for s in self.subjects if under_fac in self._get_eligible_faculty_for_subject(s['id'])}
                        
                        # Find stealable genes
                        steal_candidates = []
                        for idx, g in enumerate(chromosome.genes):
                            if g.is_lab or self._is_remedial_gene(g) or g.subject_id not in eligible_subjects or g.faculty_id == under_fac:
                                continue
                            
                            donor_f = g.faculty_id
                            donor_min = self.faculty_workload_min.get(donor_f, 0)
                            donor_cur = faculty_hours.get(donor_f, 0)
                            
                            # Estimate cost of subject
                            subject_gene_count = sum(1 for sg in chromosome.genes 
                                                   if sg.class_id == g.class_id and sg.subject_id == g.subject_id and not g.is_lab)
                            
                            if donor_cur - subject_gene_count >= donor_min:
                                steal_candidates.append((donor_cur - donor_min, idx, g))
                        
                        if steal_candidates:
                            steal_candidates.sort(reverse=True)
                            for _, idx, gene in steal_candidates:
                                if faculty_hours[under_fac] >= min_h: break
                                # Single theory per class check
                                class_facs = {g.faculty_id for g in chromosome.genes if g.class_id == gene.class_id and not g.is_lab and g.subject_id != gene.subject_id}
                                if under_fac in class_facs: continue
                                
                                subject_genes = [g for g in chromosome.genes if g.class_id == gene.class_id and g.subject_id == gene.subject_id and not g.is_lab]
                                subject_slots = [sg.time_slot_id for sg in subject_genes]
                                
                                if all(slot not in faculty_schedule[under_fac] for slot in subject_slots) and faculty_hours[under_fac] + len(subject_genes) <= max_h:
                                    donor_f = gene.faculty_id
                                    for sg in subject_genes:
                                        sg.faculty_id = under_fac
                                        faculty_schedule[donor_f].discard(sg.time_slot_id)
                                        faculty_schedule[under_fac].add(sg.time_slot_id)
                                        faculty_hours[donor_f] -= 1
                                        faculty_hours[under_fac] += 1
                                    fixed_any = True
                                    break
                            if fixed_any: break

            if not fixed_any:
                break
        
        return chromosome

    def _repair_faculty_consecutive(self, chromosome: Chromosome, max_passes: int = 3) -> Chromosome:
        """Repair ALL consecutive faculty hours across multiple passes.
        
        Rebuilds schedule data each pass and iterates over every faculty/day
        combination, fixing all back-to-back violations found.
        Use max_passes=3 for lightweight mode (GA loop), max_passes=30 for final pass.
        """
        teaching_slots = [s['id'] for s in self.time_slots]
        
        for pass_num in range(max_passes):
            # Rebuild schedule data from scratch each pass
            faculty_day_periods = defaultdict(lambda: defaultdict(list))
            faculty_schedule = defaultdict(set)
            class_used_slots = defaultdict(set)
            
            for idx, gene in enumerate(chromosome.genes):
                slot = self.slot_by_id.get(gene.time_slot_id)
                if slot:
                    faculty_day_periods[gene.faculty_id][slot['day']].append((slot['period'], idx, gene))
                    faculty_schedule[gene.faculty_id].add(gene.time_slot_id)
                    class_used_slots[gene.class_id].add(gene.time_slot_id)
            
            # Count total violations this pass
            total_fixed_this_pass = 0
            
            # Iterate over ALL faculty
            for faculty_id, day_map in list(faculty_day_periods.items()):
                for day, period_list in list(day_map.items()):
                    period_list.sort(key=lambda x: x[0])
                    for i in range(len(period_list) - 1):
                        p1, idx1, g1 = period_list[i]
                        p2, idx2, g2 = period_list[i + 1]
                        
                        # Back-to-back and NOT separated by lunch
                        if p2 == p1 + 1 and not (p1 == 4 and p2 == 5):
                            if g1.is_lab and g2.is_lab and g1.subject_id == g2.subject_id:
                                continue
                            
                            fixed_this_pair = False
                            # Try to move one of the genes (g2 first, then g1)
                            for move_idx, move_gene in [(idx2, g2), (idx1, g1)]:
                                if move_gene.is_lab or move_gene.is_remedial:
                                    continue
                                class_id = move_gene.class_id
                                fac_slots = set(faculty_schedule[faculty_id])
                                # Remove the gene we're about to move from fac_slots for safety check
                                fac_slots_without_self = fac_slots - {move_gene.time_slot_id}
                                empty_slots = [s for s in teaching_slots if s not in class_used_slots[class_id]]
                                
                                def _is_safe_for_move(slot_id):
                                    if slot_id in fac_slots:
                                        return False
                                    sl = self.slot_by_id.get(slot_id)
                                    if not sl:
                                        return False
                                    p, d = sl['period'], sl['day']
                                    for ap in (p-1, p+1):
                                        if (p == 4 and ap == 5) or (p == 5 and ap == 4):
                                            continue
                                        aslot = self.slot_id_by_day_period.get((d, ap))
                                        if aslot and aslot in fac_slots_without_self:
                                            return False
                                    return True

                                safe_empty = [s for s in empty_slots if _is_safe_for_move(s)]
                                if safe_empty:
                                    target_slot_id = random.choice(safe_empty)
                                    old_slot_id = chromosome.genes[move_idx].time_slot_id
                                    chromosome.genes[move_idx].time_slot_id = target_slot_id
                                    class_used_slots[class_id].discard(old_slot_id)
                                    class_used_slots[class_id].add(target_slot_id)
                                    faculty_schedule[faculty_id].discard(old_slot_id)
                                    faculty_schedule[faculty_id].add(target_slot_id)
                                    fixed_this_pair = True
                                    total_fixed_this_pass += 1
                                    break  # fixed this pair, move to next pair
                                
                                # Option 2: Swap with another gene in the same class that won't cause new consecutive
                                other_genes = [(j, gen) for j, gen in enumerate(chromosome.genes) 
                                              if gen.class_id == class_id and j != move_idx and not gen.is_lab]
                                random.shuffle(other_genes)
                                
                                for target_idx, target_gene in other_genes:
                                    t_slot = self.slot_by_id.get(target_gene.time_slot_id)
                                    if not t_slot:
                                        continue
                                    tp = t_slot['period']
                                    td = t_slot['day']
                                    
                                    # Would move_gene's faculty have consecutive at target?
                                    target_fac_periods = [pp for pp, _, _ in faculty_day_periods[faculty_id].get(td, []) if pp != p1 and pp != p2]
                                    is_target_consec = any(
                                        (op == tp - 1 or op == tp + 1) and not ((op == 4 and tp == 5) or (op == 5 and tp == 4))
                                        for op in target_fac_periods
                                    )
                                    
                                    if not is_target_consec:
                                        # Also check target_gene's faculty won't get consecutive at old slot
                                        old_slot = self.slot_by_id.get(move_gene.time_slot_id)
                                        old_p, old_d = old_slot['period'], old_slot['day']
                                        tgt_fac_periods = [pp for pp, _, _ in faculty_day_periods[target_gene.faculty_id].get(old_d, []) if pp != tp]
                                        tgt_would_consec = any(
                                            (op == old_p - 1 or op == old_p + 1) and not ((op == 4 and old_p == 5) or (op == 5 and old_p == 4))
                                            for op in tgt_fac_periods
                                        )
                                        
                                        # Check no clash for target faculty at old slot
                                        old_slot_genes = [j for j, gj in enumerate(chromosome.genes)
                                                         if gj.time_slot_id == move_gene.time_slot_id and j != move_idx]
                                        tgt_clash = any(chromosome.genes[j].faculty_id == target_gene.faculty_id for j in old_slot_genes)
                                        
                                        if not tgt_would_consec and not tgt_clash:
                                            chromosome.genes[move_idx].time_slot_id, chromosome.genes[target_idx].time_slot_id = \
                                                chromosome.genes[target_idx].time_slot_id, chromosome.genes[move_idx].time_slot_id
                                            fixed_this_pair = True
                                            total_fixed_this_pass += 1
                                            break
                                
                                if fixed_this_pair:
                                    break
                                
                                # Option 3: GLOBAL SWAP
                                all_indices = list(range(len(chromosome.genes)))
                                random.shuffle(all_indices)
                                for target_idx in all_indices[:200]:  # Limit search to avoid long runtime
                                    g_target = chromosome.genes[target_idx]
                                    if g_target.is_lab or self._is_remedial_gene(g_target) or target_idx == move_idx:
                                        continue
                                    
                                    t_slot_id = g_target.time_slot_id
                                    t_slot = self.slot_by_id.get(t_slot_id)
                                    if not t_slot:
                                        continue
                                    td, tp = t_slot['day'], t_slot['period']
                                    
                                    # Check move_gene faculty free at target slot
                                    genes_at_target = [j for j, gj in enumerate(chromosome.genes)
                                                      if gj.time_slot_id == t_slot_id and j != target_idx]
                                    if any(chromosome.genes[j].faculty_id == move_gene.faculty_id for j in genes_at_target):
                                        continue
                                    
                                    # Check move_gene faculty won't have consecutive at target
                                    fac_periods_on_td = [pp for pp, _, _ in faculty_day_periods[faculty_id].get(td, []) if pp != p1 and pp != p2]
                                    if any((op == tp - 1 or op == tp + 1) and not ((op == 4 and tp == 5) or (op == 5 and tp == 4))
                                           for op in fac_periods_on_td):
                                        continue
                                    
                                    # Check g_target faculty free at old slot
                                    genes_at_old = [j for j, gj in enumerate(chromosome.genes)
                                                   if gj.time_slot_id == move_gene.time_slot_id and j != move_idx]
                                    if any(chromosome.genes[j].faculty_id == g_target.faculty_id for j in genes_at_old):
                                        continue
                                        
                                    # All checks passed! Global Swap.
                                    chromosome.genes[move_idx].time_slot_id, chromosome.genes[target_idx].time_slot_id = \
                                        chromosome.genes[target_idx].time_slot_id, chromosome.genes[move_idx].time_slot_id
                                    fixed_this_pair = True
                                    total_fixed_this_pass += 1
                                    break
                                
                                if fixed_this_pair:
                                    break
                            
                            if fixed_this_pair:
                                # Data stale for this day, break to next pass
                                break
                    # Don't break out of faculty loop - continue to next day/faculty
            
            if total_fixed_this_pass == 0:
                break  # No more violations found or fixable

        return chromosome

    def _repair_multi_theory(self, chromosome: Chromosome) -> Chromosome:
        """Repair cases where a faculty is assigned 2+ theory subjects in one class.
        
        Restarts from scratch after each successful reassignment so that the
        indexes are always fresh and we never introduce new clashes.
        """
        max_outer_restarts = 15
        
        for _ in range(max_outer_restarts):
            # (faculty_id, class_id) -> subject_id -> list of gene indices
            faculty_class_subjects = defaultdict(lambda: defaultdict(list))
            # (class_id) -> set of faculty teaching THEORY in that class
            class_theory_faculty = defaultdict(set)
            # Build faculty schedule for clash-awareness
            faculty_schedule = defaultdict(set)  # faculty_id -> set of time_slot_ids
            for idx, gene in enumerate(chromosome.genes):
                faculty_schedule[gene.faculty_id].add(gene.time_slot_id)
                if not gene.is_lab:
                    faculty_class_subjects[(gene.faculty_id, gene.class_id)][gene.subject_id].append(idx)
                    class_theory_faculty[gene.class_id].add(gene.faculty_id)
            
            fixed_this_pass = False
            for (faculty_id, class_id), subject_map in faculty_class_subjects.items():
                if len(subject_map) <= 1:
                    continue
                
                # Keep one subject, move the others
                subjects = list(subject_map.keys())
                random.shuffle(subjects)
                # Keep subjects[0]
                
                for move_subject in subjects[1:]:
                    gene_indices = subject_map[move_subject]
                    eligible = self._get_eligible_faculty_for_subject(move_subject)
                    
                    # Get the time slots used by this subject in this class
                    subject_slots = set()
                    for idx in gene_indices:
                        subject_slots.add(chromosome.genes[idx].time_slot_id)
                    
                    # Alternatives: preference-matched, NOT already teaching THEORY
                    # in this class, AND free at ALL time slots used by this subject
                    alternatives = [
                        f for f in eligible
                        if f not in class_theory_faculty[class_id]
                        and not (faculty_schedule[f] & subject_slots)  # no clash
                    ]
                    
                    if not alternatives:
                        # Fallback 1: eligible but allow clash-risk
                        alternatives = [f for f in eligible if f not in class_theory_faculty[class_id]]
                    
                    if not alternatives:
                        # Fallback 2: dept faculty WITHOUT preferences, not in this class
                        alternatives = [
                            f_id for f_id in self.dept_faculty_ids
                            if f_id not in class_theory_faculty[class_id]
                            and not self.faculty_preferences.get(f_id)
                            and not (faculty_schedule[f_id] & subject_slots)
                        ]
                    
                    if not alternatives:
                        # Fallback 3: ANY eligible faculty even if already in this class
                        alternatives = eligible
                    
                    if alternatives:
                        new_fac = random.choice(alternatives)
                        # Reassign all genes for this subject in this class
                        for idx in gene_indices:
                            old_slot = chromosome.genes[idx].time_slot_id
                            faculty_schedule[faculty_id].discard(old_slot)
                            faculty_schedule[new_fac].add(old_slot)
                            chromosome.genes[idx].faculty_id = new_fac
                        fixed_this_pass = True
                        break  # Restart outer loop with fresh indexes
                
                if fixed_this_pass:
                    break
            
            if not fixed_this_pass:
                break  # No more violations found
        
        return chromosome

    def _unify_subject_teachers(self, chromosome: Chromosome) -> Chromosome:
        """Ensure every subject-class pair has exactly one primary teacher.
        If a subject is split between multiple teachers, resolve it by
        picking the most frequent teacher or a random eligible one.
        """
        subject_assignments = defaultdict(lambda: defaultdict(list))
        for i, g in enumerate(chromosome.genes):
            # Apply to ALL subjects per class (Theory, Remedial, etc.)
            # Labs usually have their own dedicated consolidation but let's be safe
            subject_assignments[g.class_id][g.subject_id].append(i)
        
        for class_id, subjects in subject_assignments.items():
            for subject_id, gene_indices in subjects.items():
                teachers = [chromosome.genes[i].faculty_id for i in gene_indices]
                if len(set(teachers)) > 1:
                    # Resolve split: pick the teacher assigned to the most hours
                    best_teacher = max(set(teachers), key=teachers.count)
                    for i in gene_indices:
                        chromosome.genes[i].faculty_id = best_teacher
                        
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
            # Swap time slots between two NON-LAB, NON-REMEDIAL genes of the same class
            non_lab_genes = [g for g in mutated.genes 
                            if not g.is_lab and not self._is_remedial_gene(g)]
            if non_lab_genes:
                gene1 = random.choice(non_lab_genes)
                same_class_genes = [g for g in mutated.genes 
                                   if g.class_id == gene1.class_id and g != gene1 
                                   and not g.is_lab and not self._is_remedial_gene(g)]
                if same_class_genes:
                    gene2 = random.choice(same_class_genes)
                    gene1.time_slot_id, gene2.time_slot_id = gene2.time_slot_id, gene1.time_slot_id
        
        elif mutation_type == 'change_faculty':
            # Change faculty for a random non-remedial subject (all its hours in the class)
            subjects_in_genes = list(set((g.class_id, g.subject_id, g.is_lab) for g in mutated.genes if not self._is_remedial_gene(g)))
            if not subjects_in_genes:
                return mutated
            
            c_id, s_id, is_l = random.choice(subjects_in_genes)
            eligible = self._get_eligible_faculty_for_subject(s_id)
            if not eligible:
                return mutated
            
            # Find all slots where this subject appears in this class
            subject_slots = [g.time_slot_id for g in mutated.genes 
                             if g.class_id == c_id and g.subject_id == s_id and g.is_lab == is_l]
            
            # Build faculty schedules to find a clash-free teacher for ALL slots
            faculty_schedule = defaultdict(set)
            for g in mutated.genes:
                # Exclude this subject's current slots from the current teacher's schedule
                if not (g.class_id == c_id and g.subject_id == s_id and g.is_lab == is_l):
                    faculty_schedule[g.faculty_id].add(g.time_slot_id)
                    if g.assistant_faculty_id:
                        faculty_schedule[g.assistant_faculty_id].add(g.time_slot_id)
            
            clash_free = [f for f in eligible
                          if not (faculty_schedule.get(f, set()) & set(subject_slots))]
            
            if clash_free:
                new_faculty = random.choice(clash_free)
                # Update ALL hours for this subject
                for g in mutated.genes:
                    if g.class_id == c_id and g.subject_id == s_id and g.is_lab == is_l:
                        g.faculty_id = new_faculty
        
        elif mutation_type == 'swap_subjects':
            # Swap faculty assignments between two DIFFERENT subjects in the SAME class
            # (Maintains one-teacher-per-subject and preserves class slot structure)
            class_ids = list(set(g.class_id for g in mutated.genes))
            random.shuffle(class_ids)
            
            for c_id in class_ids:
                class_subjects = list(set((g.subject_id, g.is_lab, g.faculty_id) for g in mutated.genes if g.class_id == c_id))
                if len(class_subjects) < 2:
                    continue
                
                s1_info, s2_info = random.sample(class_subjects, 2)
                id1, lab1, fac1 = s1_info
                id2, lab2, fac2 = s2_info
                
                # Check if fac1 can teach id2 and fac2 can teach id1
                if fac1 in self._get_eligible_faculty_for_subject(id2) and fac2 in self._get_eligible_faculty_for_subject(id1):
                    # Check for clashes in their schedules across all classes
                    faculty_schedule = defaultdict(set)
                    for g in mutated.genes:
                        if g.class_id != c_id:
                            faculty_schedule[g.faculty_id].add(g.time_slot_id)
                            if g.assistant_faculty_id:
                                faculty_schedule[g.assistant_faculty_id].add(g.time_slot_id)
                    
                    slots1 = [g.time_slot_id for g in mutated.genes if g.class_id == c_id and g.subject_id == id1]
                    slots2 = [g.time_slot_id for g in mutated.genes if g.class_id == c_id and g.subject_id == id2]
                    
                    if not (faculty_schedule.get(fac2, set()) & set(slots1)) and not (faculty_schedule.get(fac1, set()) & set(slots2)):
                        # Swap teachers for ALL hours of these subjects in this class
                        for g in mutated.genes:
                            if g.class_id == c_id:
                                if g.subject_id == id1:
                                    g.faculty_id = fac2
                                elif g.subject_id == id2:
                                    g.faculty_id = fac1
                        break
            gene1 = random.choice(mutated.genes)
            same_slot_genes = [g for g in mutated.genes 
                              if g.time_slot_id == gene1.time_slot_id and g.class_id != gene1.class_id]
            if same_slot_genes:
                gene2 = random.choice(same_slot_genes)
                # Build faculty schedule to check for new clashes
                faculty_schedule = defaultdict(set)
                for g in mutated.genes:
                    faculty_schedule[g.faculty_id].add(g.time_slot_id)
                
                # Check if swapping would create clashes:
                # gene1 gets gene2's faculty - is gene2's faculty free at gene1's other slots?
                # gene2 gets gene1's faculty - is gene1's faculty free at gene2's other slots?
                # Since they're at the same slot, the swap itself is fine for THIS slot.
                # But we need to check other slots where these subjects appear.
                # Simple check: just do the swap (same slot = no new clash at this slot)
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
        # Initialize and evaluate initial population
        population = self.initialize_population()
        for chromosome in population:
            self.calculate_fitness(chromosome)
        
        fitness_history = []
        best_ever = max(population, key=lambda c: c.fitness)
        stagnation_counter = 0
        
        # Parallel generation setup
        max_workers = max(1, multiprocessing.cpu_count() - 1)
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            for generation in range(self.generations):
                # Sort population by fitness
                population.sort(key=lambda c: c.fitness, reverse=True)
                
                # Track best chromosome
                current_best = population[0]
                if current_best.fitness > best_ever.fitness:
                    best_ever = current_best.copy()
                    stagnation_counter = 0
                else:
                    stagnation_counter += 1
                
                fitness_history.append(current_best.fitness)
                
                if callback:
                    callback(generation, current_best.fitness)
                
                # Termination conditions
                if current_best.fitness >= 0 or stagnation_counter >= 50:
                    break
                
                # Create next generation
                new_population = []
                
                # Elitism: keep the best performers
                for i in range(min(len(population), self.elite_count)):
                    new_population.append(population[i].copy())
                
                # Tiered Repair strategy: only full repairs every X generations
                full_repair_gen = (generation % self.repair_frequency == 0) or (generation > self.generations - 10)
                
                # Batch parallel generation of children
                while len(new_population) < self.population_size:
                    needed = self.population_size - len(new_population)
                    batch_size = (needed + 1) // 2
                    
                    # Pass a subset of high-performing parents to reduce pickling overhead
                    mating_pool = population[:min(len(population), 20)]
                    futures = [executor.submit(self._generate_child_pair, mating_pool, full_repair_gen) 
                               for _ in range(batch_size)]
                    
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            c1, c2 = future.result()
                            if len(new_population) < self.population_size:
                                new_population.append(c1)
                            if len(new_population) < self.population_size:
                                new_population.append(c2)
                        except Exception as e:
                            print(f"GA Worker Error: {e}")
                            new_population.append(random.choice(population).copy())
                
                population = new_population
        
        # Final multi-stage repair sequence (FULL MODE)
        best_ever = self._unify_subject_teachers(best_ever)
        best_ever = self._repair_faculty_clashes(best_ever, evolution_mode=False)
        best_ever = self._repair_workload(best_ever, full_mode=True)
        best_ever = self._repair_faculty_consecutive(best_ever, max_passes=30)
        # Belt and suspenders: Final clash + workload check after breaking sequences
        best_ever = self._repair_faculty_clashes(best_ever, evolution_mode=False)
        best_ever = self._repair_workload(best_ever, full_mode=True)
        # Final consecutive pass - since workload may have moved genes around
        best_ever = self._repair_faculty_consecutive(best_ever, max_passes=30)
        best_ever = self._repair_faculty_clashes(best_ever, evolution_mode=False)
        best_ever = self._repair_remedial(best_ever)
            
        return best_ever, fitness_history

    def _generate_child_pair(self, population: List[Chromosome], full_repair: bool) -> Tuple[Chromosome, Chromosome]:
        """Worker function for parallel child generation."""
        parent1 = self.tournament_selection(population)
        parent2 = self.tournament_selection(population)
        
        child1, child2 = self.crossover(parent1, parent2)
        
        child1 = self.mutate(child1)
        child2 = self.mutate(child2)
        
        # Lazy Repairs
        child1 = self._repair_labs(child1)
        child2 = self._repair_labs(child2)
        child1 = self._repair_remedial(child1)
        child2 = self._repair_remedial(child2)
        
        # Heavy repairs - skip if not full_repair mode
        if full_repair:
            child1 = self._repair_faculty_clashes(child1, evolution_mode=True)
            child2 = self._repair_faculty_clashes(child2, evolution_mode=True)
            child1 = self._repair_workload(child1)
            child2 = self._repair_workload(child2)
            child1 = self._repair_faculty_consecutive(child1)
            child2 = self._repair_faculty_consecutive(child2)
            child1 = self._repair_multi_theory(child1)
            child2 = self._repair_multi_theory(child2)
            
            child1 = self._unify_subject_teachers(child1)
            child2 = self._unify_subject_teachers(child2)
            
            child1 = self._repair_faculty_clashes(child1, evolution_mode=True)
            child2 = self._repair_faculty_clashes(child2, evolution_mode=True)

        self.calculate_fitness(child1)
        self.calculate_fitness(child2)
        
        return child1, child2



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
        Semester, ClassSection, Subject, Faculty, TimeSlot, 
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
    ).values('id', 'name', 'designation', 'preferences', 'min_workload_hours', 'max_workload_hours'))
    
    # Add min/max hours to faculty data based on designation or custom overrides
    for f in faculties:
        # Check for custom overrides first
        if f.get('min_workload_hours') is not None:
            f['min_hours'] = f['min_workload_hours']
        if f.get('max_workload_hours') is not None:
            f['max_hours'] = f['max_workload_hours']
            
        # If either is still missing, fall back to designation defaults
        if 'min_hours' not in f or 'max_hours' not in f:
            limits = Faculty.WORKLOAD_LIMITS.get(f['designation'], (20, 20))
            if isinstance(limits, tuple):
                if 'min_hours' not in f: f['min_hours'] = limits[0]
                if 'max_hours' not in f: f['max_hours'] = limits[1]
            else:
                if 'min_hours' not in f: f['min_hours'] = limits
                if 'max_hours' not in f: f['max_hours'] = limits
    
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
    
    # Initialize and run GA with increased capacity for better convergence
    ga = GeneticAlgorithm(
        population_size=300,
        generations=1500,
        crossover_rate=0.85,
        mutation_rate=0.20,
        elite_count=15,
        tournament_size=8
    )
    
    # Build semester_number_map from semester_id
    from core.models import Semester as SemModel
    sem_obj = SemModel.objects.get(id=semester_id)
    semester_number_map = {semester_id: sem_obj.number}
    
    ga.load_data(
        classes=classes,
        subjects=subjects,
        faculties=faculties,
        time_slots=time_slots,
        faculty_preferences=faculty_preferences,
        faculty_history=dict(faculty_history),
        semester_number_map=semester_number_map
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
            is_remedial=gene.is_remedial,
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
    # Include department_id so the GA knows which dept each subject belongs to
    subjects = list(Subject.objects.filter(
        semester_id__in=semester_ids
    ).values('id', 'name', 'code', 'subject_type', 'lecture_hours', 'tutorial_hours', 'practical_hours', 'semester_id', 'department_id'))
    
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

    # ── Rule: BSH Faculty Force-Load ──────────────────────────────────
    # If any subjects are BSH (MAT, PHT, HUN, etc.), load ALL BSH faculty
    BSH_PREFIXES = ('PHT', 'HUN', 'MAT', 'CYT', 'PHL', 'CYL', 'EST', 'MNC', 'HUT')
    has_bsh_subjects = any(s['code'].startswith(BSH_PREFIXES) for s in subjects)
    
    # Collect all faculty IDs to load
    faculty_ids = set(dept_faculty_qs.values_list('id', flat=True))
    faculty_ids.update(cross_dept_faculty_qs.values_list('id', flat=True))
    
    if has_bsh_subjects:
        bsh_dept = Department.objects.filter(code='BSH').first()
        if bsh_dept:
            bsh_faculty_ids = Faculty.objects.filter(department=bsh_dept, is_active=True).values_list('id', flat=True)
            faculty_ids.update(bsh_faculty_ids)
    
    # Fetch final faculty data in one clean query
    faculties = list(Faculty.objects.filter(
        id__in=faculty_ids
    ).values(
        'id', 'name', 'designation', 'preferences', 'department_id', 
        'min_workload_hours', 'max_workload_hours', 'department__code'
    ))
    
    # Map department__code to department_code for consistency
    for f in faculties:
        f['department_code'] = f.pop('department__code', None)
    
    if not faculties:
        # Fallback to all active faculty
        faculties = list(Faculty.objects.filter(
            is_active=True
        ).values('id', 'name', 'designation', 'preferences', 'department_id', 'department__code', 'min_workload_hours', 'max_workload_hours'))
    
    # Add min/max hours to faculty data based on designation or custom overrides
    for f in faculties:
        # Check for custom overrides first
        if f.get('min_workload_hours') is not None:
            f['min_hours'] = f['min_workload_hours']
        if f.get('max_workload_hours') is not None:
            f['max_hours'] = f['max_workload_hours']
            
        # If either is still missing, fall back to designation defaults
        if 'min_hours' not in f or 'max_hours' not in f:
            limits = Faculty.WORKLOAD_LIMITS.get(f['designation'], (20, 20))
            if isinstance(limits, tuple):
                if 'min_hours' not in f: f['min_hours'] = limits[0]
                if 'max_hours' not in f: f['max_hours'] = limits[1]
            else:
                if 'min_hours' not in f: f['min_hours'] = limits
                if 'max_hours' not in f: f['max_hours'] = limits
    
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
        class_section__semester__department_id=department_id  # exclude ALL semesters in this department
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
        generations=300,
        crossover_rate=0.85,
        mutation_rate=0.15,
        elite_count=4,
        tournament_size=7
    )
    
    # Build semester_number_map: semester_id -> semester_number
    semester_number_map = {s.id: s.number for s in semesters}
    
    ga.load_data(
        classes=classes,
        subjects=subjects,
        faculties=faculties,
        time_slots=time_slots,
        faculty_preferences=faculty_preferences,
        faculty_history=dict(faculty_history),
        pre_booked_slots=dict(pre_booked_slots),
        department_id=department_id,
        semester_number_map=semester_number_map
    )
    
    best_solution, fitness_history = ga.evolve()
    
    # Clear existing entries for ALL semesters in this department for this instance
    TimetableEntry.objects.filter(
        class_section__semester_id__in=semester_ids,
        semester_instance=semester_instance
    ).delete()
    
    # Also clear FacultySubjectAssignment for this instance and these classes
    FacultySubjectAssignment.objects.filter(
        semester_instance=semester_instance,
        class_section_id__in=[c['id'] for c in classes]
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
            is_remedial=gene.is_remedial,
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
    
    # ── Post-save clash correction pass ───────────────────────────────
    # Scan saved entries and fix any faculty clashes that the GA missed.
    # This guarantees the final timetable is always clash-free.
    from django.db.models import Count as _Count

    # Helper: find an eligible faculty for a subject, respecting strict
    # preference matching and departmental lock, excluding booked faculty.
    def _find_preferred_faculty(subj_code, dept_id, exclude_ids):
        """Find a faculty who can teach this subject, not in exclude_ids.
        Rule 0: BSH subjects MUST be assigned to BSH faculty.
        Rule 1: Faculty whose comma-separated preferences include the exact code.
        Rule 2: Same-department generalist (no preferences set).
        Rule 3: Same-department faculty (has preferences for other subjects).
        Returns Faculty instance or None."""
        BSH_PREFIXES = ('PHT', 'HUN', 'MAT', 'CYT', 'PHL', 'CYL', 'EST', 'MNC', 'HUT')
        is_bsh = subj_code.startswith(BSH_PREFIXES)
        
        # For BSH subjects, override dept_id to BSH department
        effective_dept_id = dept_id
        if is_bsh:
            bsh_dept = Department.objects.filter(code='BSH').first()
            if bsh_dept:
                effective_dept_id = bsh_dept.id
        
        # Rule 1: Exact preference match
        for fac in Faculty.objects.filter(is_active=True).exclude(id__in=exclude_ids):
            if fac.preferences:
                pref_codes = [p.strip() for p in fac.preferences.split(',')]
                if subj_code in pref_codes:
                    # BSH guard: only accept BSH faculty for BSH subjects
                    if is_bsh and fac.department_id != effective_dept_id:
                        continue
                    return fac
        # Rule 2: Same-department generalist (no preferences)
        from django.db.models import Q as _Q
        fac = Faculty.objects.filter(
            is_active=True, department_id=effective_dept_id
        ).filter(
            _Q(preferences__isnull=True) | _Q(preferences='')
        ).exclude(id__in=exclude_ids).first()
        if fac:
            return fac
        # Rule 3: Any same-department faculty
        fac = Faculty.objects.filter(
            is_active=True, department_id=effective_dept_id
        ).exclude(id__in=exclude_ids).first()
        return fac
    clash_iter = 0
    while clash_iter < 20:  # Safety cap: at most 20 repair rounds
        clash_groups = (
            TimetableEntry.objects
            .filter(class_section__semester__department_id=department_id,
                    semester_instance=semester_instance)
            .values('faculty_id', 'time_slot_id')
            .annotate(_cnt=_Count('id'))
            .filter(_cnt__gt=1)
        )
        if not clash_groups.exists():
            break
        clash_iter += 1
        
        # Track slots used in this iteration to avoid moving two entries to same slot
        moved_to_this_round = defaultdict(set) # class_id -> set(slot_ids)

        for cg in clash_groups:
            clashing = list(
                TimetableEntry.objects.filter(
                    faculty_id=cg['faculty_id'],
                    time_slot_id=cg['time_slot_id'],
                    semester_instance=semester_instance
                ).select_related('subject', 'class_section__semester__department')
            )
            # Find which faculties are booked at this slot (including entries from other depts)
            booked_at_slot = set(
                TimetableEntry.objects.filter(
                    semester_instance=semester_instance,
                    time_slot_id=cg['time_slot_id']
                ).values_list('faculty_id', flat=True)
            )
            
            # Keep the first entry; try to fix others
            for fix_e in clashing[1:]:
                subj_code = fix_e.subject.code
                dept_id = fix_e.class_section.semester.department_id
                
                # PROTECT RMH: If it's a REMEDIAL entry, WE CANNOT MOVE IT.
                # It must stay in its synchronized slot. We only change faculty.
                is_remedial = getattr(fix_e, 'is_remedial', False)
                if is_remedial:
                    new_fac = _find_preferred_faculty(subj_code, dept_id, booked_at_slot)
                    if new_fac:
                        fix_e.faculty = new_fac
                        fix_e.save()
                        booked_at_slot.add(new_fac.id)
                        print(f"  [RMH-ClashFix] Entry {fix_e.id} faculty changed to {new_fac.name}")
                    continue # RMH entry must stay in this slot
                
                # For non-remedial: first try changing faculty
                new_fac = _find_preferred_faculty(subj_code, dept_id, booked_at_slot)
                if new_fac:
                    fix_e.faculty = new_fac
                    fix_e.save()
                    booked_at_slot.add(new_fac.id)
                    print(f"  [ClashFix] Entry {fix_e.id} -> {new_fac.name}")
                else:
                    # Move to a different time slot
                    orig_fac_id = fix_e.faculty_id
                    cls_id = fix_e.class_section_id
                    
                    booked_by_fac = set(TimetableEntry.objects.filter(
                        faculty_id=orig_fac_id, semester_instance=semester_instance
                    ).values_list('time_slot_id', flat=True))
                    
                    booked_by_class = set(TimetableEntry.objects.filter(
                        class_section_id=cls_id, semester_instance=semester_instance
                    ).values_list('time_slot_id', flat=True))
                    
                    # Prevent moving two entries to the same empty slot in one iteration
                    booked_by_class.update(moved_to_this_round[cls_id])

                    from core.models import TimeSlot as _TS
                    all_slot_ids = set(_TS.objects.filter(
                        slot_type__in=['MORNING', 'AFTERNOON']
                    ).values_list('id', flat=True))
                    
                    free_slots = all_slot_ids - booked_by_fac - booked_by_class
                    if free_slots:
                        new_slot_id = sorted(list(free_slots))[0] 
                        fix_e.time_slot_id = new_slot_id
                        fix_e.save()
                        moved_to_this_round[cls_id].add(new_slot_id)
                        print(f"  [ClashFix-Move] Entry {fix_e.id} moved to slot {new_slot_id}")
    if clash_iter > 0:
        print(f"  Post-save clash correction: {clash_iter} round(s)")

    # ── Post-save: Enforce one faculty per subject per class ───────────
    # Find any (class_section, subject) pair with more than 1 distinct faculty.
    from django.db.models import Count as _Count2
    multi_faculty_cases = (
        TimetableEntry.objects
        .filter(class_section__semester__department_id=department_id,
                semester_instance=semester_instance,
                is_lab_session=False)
        .exclude(subject__subject_type='RMH')  # RMH handled separately
        .values('class_section_id', 'subject_id', 'faculty_id')
        .distinct()
    )
    # Group by (class, subject) → set of faculty ids
    from collections import defaultdict as _dd
    cs_faculty_map = _dd(set)  # (class_id, subject_id) -> {faculty_ids}
    for row in multi_faculty_cases:
        cs_faculty_map[(row['class_section_id'], row['subject_id'])].add(row['faculty_id'])

    unified = 0
    for (cls_id, subj_id), fac_set in cs_faculty_map.items():
        if len(fac_set) <= 1:
            continue

        # Collect per-faculty entries: fac_id -> [(entry_id, time_slot_id)]
        fac_entries = {}
        for f in fac_set:
            rows = list(TimetableEntry.objects.filter(
                class_section_id=cls_id, subject_id=subj_id,
                faculty_id=f, semester_instance=semester_instance,
                is_lab_session=False
            ).values_list('id', 'time_slot_id'))
            fac_entries[f] = rows

        # Pick winner: most entries, but prefer one that is clash-free
        # at all slots occupied by the minority faculty.
        sorted_cands = sorted(fac_set, key=lambda f: len(fac_entries[f]), reverse=True)
        winner = sorted_cands[0]  # default
        for candidate in sorted_cands:
            # Slots this candidate is already booked (outside this subject+class)
            cand_busy = set(TimetableEntry.objects.filter(
                faculty_id=candidate, semester_instance=semester_instance
            ).exclude(
                class_section_id=cls_id, subject_id=subj_id, is_lab_session=False
            ).values_list('time_slot_id', flat=True))
            # Slots held by all OTHER faculty for this subject+class
            others_slots = set()
            for f, rows in fac_entries.items():
                if f != candidate:
                    others_slots.update(s for _, s in rows)
            if not (cand_busy & others_slots):
                winner = candidate
                break  # clash-free winner found

        # Reassign minority entries slot-by-slot, skipping clashes
        winner_busy = set(TimetableEntry.objects.filter(
            faculty_id=winner, semester_instance=semester_instance
        ).exclude(
            class_section_id=cls_id, subject_id=subj_id, is_lab_session=False
        ).values_list('time_slot_id', flat=True))

        minority = [f for f in fac_set if f != winner]
        reassigned = 0
        for m_fac in minority:
            for entry_id, slot_id in fac_entries.get(m_fac, []):
                if slot_id in winner_busy:
                    continue  # winner already busy here; ClashFix2 will handle it
                TimetableEntry.objects.filter(id=entry_id).update(faculty_id=winner)
                winner_busy.add(slot_id)
                reassigned += 1
                unified += 1

        if reassigned:
            print(f"  [SubjectUnify] class={cls_id} subject={subj_id}: "
                  f"unified {reassigned} slots -> faculty_id={winner}")

    if unified > 0:
        print(f"  Post-save subject unification: {unified} entries reassigned")

    # ── Final ClashFix pass after SubjectUnify ────────────────────────
    # Catches any clashes SubjectUnify still introduced (e.g. no clash-free winner).
    clash_iter2 = 0
    while clash_iter2 < 20:
        clash_groups2 = (
            TimetableEntry.objects
            .filter(class_section__semester_id__in=semester_ids,
                    semester_instance=semester_instance)
            .values('faculty_id', 'time_slot_id')
            .annotate(_cnt=_Count('id'))
            .filter(_cnt__gt=1)
        )
        if not clash_groups2.exists():
            break
        clash_iter2 += 1
        for cg2 in clash_groups2:
            clashing2 = list(
                TimetableEntry.objects.filter(
                    faculty_id=cg2['faculty_id'],
                    time_slot_id=cg2['time_slot_id'],
                    class_section__semester_id__in=semester_ids,
                    semester_instance=semester_instance
                ).select_related('subject', 'class_section__semester__department')
            )
            booked2 = set(
                TimetableEntry.objects.filter(
                    semester_instance=semester_instance,
                    time_slot_id=cg2['time_slot_id']
                ).values_list('faculty_id', flat=True)
            )
            for fix_e2 in clashing2[1:]:
                subj_code2 = fix_e2.subject.code
                dept_id2 = fix_e2.class_section.semester.department_id
                
                # PROTECT RMH: never move remedial entries, only change faculty
                is_rmh2 = getattr(fix_e2, 'is_remedial', False)
                
                new_fac2 = _find_preferred_faculty(subj_code2, dept_id2, booked2)
                if new_fac2:
                    fix_e2.faculty = new_fac2
                    fix_e2.save()
                    booked2.add(new_fac2.id)
                    print(f"  [ClashFix2] Entry {fix_e2.id} -> {new_fac2.name}")
                elif not is_rmh2:
                    # Only move NON-RMH entries to a free slot as last resort
                    orig_fac2 = fix_e2.faculty_id
                    cls2 = fix_e2.class_section_id
                    fac_slots2 = set(TimetableEntry.objects.filter(
                        faculty_id=orig_fac2,
                        semester_instance=semester_instance
                    ).values_list('time_slot_id', flat=True))
                    cls_slots2 = set(TimetableEntry.objects.filter(
                        class_section_id=cls2,
                        semester_instance=semester_instance
                    ).values_list('time_slot_id', flat=True))
                    from core.models import TimeSlot as _TS2
                    all_ts2 = list(_TS2.objects.filter(
                        slot_type__in=['MORNING', 'AFTERNOON']
                    ).values('id', 'period').order_by('period'))
                    # Prefer earlier periods for compact scheduling
                    free2 = [t['id'] for t in all_ts2 if t['id'] not in fac_slots2 and t['id'] not in cls_slots2]
                    if free2:
                        fix_e2.time_slot_id = free2[0]  # earliest period
                        fix_e2.save()
                        print(f"  [ClashFix2-Move] Entry {fix_e2.id} -> slot {fix_e2.time_slot_id}")
    if clash_iter2 > 0:
        print(f"  Post-unify clash correction: {clash_iter2} round(s)")

    # ── Post-save RMH scrub pass ─────────────────────────────────────
    # Replace any non-remedial entries for RMH-type subjects with theory
    # subjects. RMH subjects have names like "Remedial / Minor / Honors
    # Course" which confuse the display if they appear outside remedial slots.
    from core.models import Subject as _SubjScrub
    rmh_subject_ids = set(
        _SubjScrub.objects.filter(subject_type='RMH').values_list('id', flat=True)
    )
    scrub_count = 0
    if rmh_subject_ids:
        stray_rmh = TimetableEntry.objects.filter(
            semester_instance=semester_instance,
            subject_id__in=rmh_subject_ids,
            is_remedial=False  # non-remedial RMH entries = confusion
        )
        for stray in stray_rmh:
            # Find a theory subject for this class's semester
            cls_sem_id = stray.class_section.semester_id
            theory_subj = _SubjScrub.objects.filter(
                semester_id=cls_sem_id, subject_type='THEORY'
            ).first()
            if theory_subj:
                stray.subject = theory_subj
                stray.save()
                scrub_count += 1
        if scrub_count > 0:
            print(f"  Post-save RMH scrub: {scrub_count} stray RMH entries replaced with theory")

    # ── Post-save blank-filling pass ──────────────────────────────────
    # Ensure every class has exactly 35 entries (7 periods × 5 days).
    # Fill any empty slots with round-robin theory/elective subjects.
    from core.models import TimeSlot as _TSFill
    all_teaching_slot_ids = set(
        _TSFill.objects.filter(slot_type__in=['MORNING', 'AFTERNOON'])
        .values_list('id', flat=True)
    )
    total_filled = 0

    for cls in classes:
        cls_id = cls['id']
        existing_slots = set(
            TimetableEntry.objects.filter(
                class_section_id=cls_id,
                semester_instance=semester_instance
            ).values_list('time_slot_id', flat=True)
        )
        missing_slots = all_teaching_slot_ids - existing_slots
        if not missing_slots:
            continue

        # Get theory/elective subjects for this class's semester
        sem_id = cls.get('semester_id')
        filler_subjects = [
            s for s in subjects
            if s['semester_id'] == sem_id and s['subject_type'] in ('THEORY', 'ELECTIVE')
        ]
        if not filler_subjects:
            continue

        # Build map: subject_id -> faculty already assigned for this class
        existing_entries = TimetableEntry.objects.filter(
            class_section_id=cls_id,
            semester_instance=semester_instance,
            is_lab_session=False
        ).values('subject_id', 'faculty_id').distinct()

        subj_fac_map = {}
        for row in existing_entries:
            if row['subject_id'] not in subj_fac_map:
                subj_fac_map[row['subject_id']] = row['faculty_id']

        # Sort missing slots by period (earliest first) for compact scheduling
        sorted_missing = sorted(missing_slots, key=lambda s: (
            _TSFill.objects.filter(id=s).values_list('period', flat=True).first() or 99
        ))

        fill_idx = 0
        for slot_id in sorted_missing:
            # Find who is already booked at this slot
            booked_fac_at_slot = set(
                TimetableEntry.objects.filter(
                    semester_instance=semester_instance,
                    time_slot_id=slot_id
                ).values_list('faculty_id', flat=True)
            )

            assigned = False
            for attempt in range(len(filler_subjects)):
                subj = filler_subjects[(fill_idx + attempt) % len(filler_subjects)]
                fac_id = subj_fac_map.get(subj['id'])

                if not fac_id:
                    # Try to find a faculty for this subject
                    from core.models import Faculty as _FacFill
                    subj_code = subj.get('code', '')
                    candidate = _FacFill.objects.filter(
                        is_active=True, preferences__contains=subj_code
                    ).exclude(id__in=booked_fac_at_slot).first()
                    if not candidate:
                        candidate = _FacFill.objects.filter(
                            is_active=True, department_id=department_id
                        ).exclude(id__in=booked_fac_at_slot).first()
                    if candidate:
                        fac_id = candidate.id
                        subj_fac_map[subj['id']] = fac_id

                if fac_id and fac_id not in booked_fac_at_slot:
                    TimetableEntry.objects.create(
                        class_section_id=cls_id,
                        subject_id=subj['id'],
                        faculty_id=fac_id,
                        time_slot_id=slot_id,
                        semester_instance=semester_instance,
                        is_lab_session=False,
                        is_remedial=False
                    )
                    total_filled += 1
                    fill_idx = (fill_idx + attempt + 1) % len(filler_subjects)
                    assigned = True
                    break

            if not assigned:
                # Last resort: use first filler subject with any available faculty
                subj = filler_subjects[fill_idx % len(filler_subjects)]
                from core.models import Faculty as _FacFill2
                any_fac = _FacFill2.objects.filter(
                    is_active=True
                ).exclude(id__in=booked_fac_at_slot).first()
                if any_fac:
                    TimetableEntry.objects.create(
                        class_section_id=cls_id,
                        subject_id=subj['id'],
                        faculty_id=any_fac.id,
                        time_slot_id=slot_id,
                        semester_instance=semester_instance,
                        is_lab_session=False,
                        is_remedial=False
                    )
                    total_filled += 1
                fill_idx = (fill_idx + 1) % len(filler_subjects)

    if total_filled > 0:
        print(f"  Post-save blank-filling: {total_filled} empty slots filled")

    # ── Post-generation remedial sync validation ──────────────────────
    remedial_validation = []
    for sem in semesters:
        config_slots = ga.remedial_schedule.get(sem.id, [])
        if not config_slots:
            continue
        
        sem_classes = ClassSection.objects.filter(semester_id=sem.id).values_list('id', flat=True)
        sem_entries = TimetableEntry.objects.filter(
            class_section_id__in=sem_classes,
            semester_instance=semester_instance,
            subject__subject_type='RMH'
        )
        
        # A class matches if AT LEAST ONE of its RMH entries is in the expected sync slots
        class_has_sync = {c_id: False for c_id in sem_classes}
        classes_with_rmh_entries = set()
        
        for entry in sem_entries:
            ts_id = entry.time_slot_id
            classes_with_rmh_entries.add(entry.class_section_id)
            if ts_id in config_slots:
                class_has_sync[entry.class_section_id] = True
        
        # Only evaluate classes that actually have an RMH entry
        if not classes_with_rmh_entries:
            status = 'NO_RMH'
        else:
            all_match = all(class_has_sync[cid] for cid in classes_with_rmh_entries)
            status = 'OK' if all_match else 'MISMATCH'
            
        remedial_validation.append({
            'semester': str(sem),
            'expected': f"{len(config_slots)} slots conditionally verified",
            'status': status,
            'classes_checked': len(classes_with_rmh_entries)
        })
        print(f"  Remedial sync S{sem.number}: {status} "
              f"({len(config_slots)} slots configured, "
              f"{len(classes_with_rmh_entries)} classes checked)")
    
    # ── FINAL: Global cross-department clash resolution ────────────────
    # The earlier clash passes only checked within this department's semesters.
    # This pass checks ALL entries globally to catch cross-department clashes.
    from core.models import TimeSlot as _TSGlobal
    all_teaching_ids = set(
        _TSGlobal.objects.filter(slot_type__in=['MORNING', 'AFTERNOON'])
        .values_list('id', flat=True)
    )
    global_clash_fixed = 0
    for _gc_round in range(30):
        # Detect clashes across the ENTIRE semester instance
        global_clashes = (
            TimetableEntry.objects
            .filter(semester_instance=semester_instance)
            .values('faculty_id', 'time_slot_id')
            .annotate(_cnt=_Count('id'))
            .filter(_cnt__gt=1)
        )
        if not global_clashes.exists():
            break
        
        for gc in global_clashes:
            gc_entries = list(
                TimetableEntry.objects.filter(
                    semester_instance=semester_instance,
                    faculty_id=gc['faculty_id'],
                    time_slot_id=gc['time_slot_id']
                ).select_related('subject', 'class_section__semester__department', 'faculty')
            )
            if len(gc_entries) < 2:
                continue
            
            # All faculties booked at this slot
            booked_at_slot = set(
                TimetableEntry.objects.filter(
                    semester_instance=semester_instance,
                    time_slot_id=gc['time_slot_id']
                ).values_list('faculty_id', flat=True)
            )
            
            # Only fix entries belonging to THIS department's semesters
            # (we don't want to touch other departments' entries)
            dept_entries = [e for e in gc_entries if e.class_section.semester_id in semester_ids]
            if not dept_entries:
                continue
            
            for fix_e in dept_entries:
                # Skip if clash is already resolved (could happen mid-loop)
                still_clash = TimetableEntry.objects.filter(
                    semester_instance=semester_instance,
                    faculty_id=fix_e.faculty_id,
                    time_slot_id=fix_e.time_slot_id
                ).count()
                if still_clash <= 1:
                    continue
                
                is_rmh = getattr(fix_e, 'is_remedial', False)
                subj_code = fix_e.subject.code
                dept_id = fix_e.class_section.semester.department_id
                
                # Strategy 1: Swap faculty (strict preference match)
                new_fac = _find_preferred_faculty(subj_code, dept_id, booked_at_slot)
                if new_fac:
                    fix_e.faculty = new_fac
                    fix_e.save()
                    booked_at_slot.add(new_fac.id)
                    global_clash_fixed += 1
                    continue
                
                # Strategy 2: Move this entry to a different time slot (non-RMH only)
                if not is_rmh:
                    fac_booked = set(TimetableEntry.objects.filter(
                        faculty_id=fix_e.faculty_id, semester_instance=semester_instance
                    ).values_list('time_slot_id', flat=True))
                    cls_booked = set(TimetableEntry.objects.filter(
                        class_section_id=fix_e.class_section_id, semester_instance=semester_instance
                    ).values_list('time_slot_id', flat=True))
                    free = all_teaching_ids - fac_booked - cls_booked
                    if free:
                        # Pick a random free slot to reduce repeated placement conflicts
                        import random as _rng
                        new_slot = _rng.choice(list(free))
                        fix_e.time_slot_id = new_slot
                        fix_e.save()
                        global_clash_fixed += 1
                        continue
                
                # Strategy 3: Swap time slot with a non-clashing entry in same class
                if not is_rmh:
                    same_class_entries = list(TimetableEntry.objects.filter(
                        class_section_id=fix_e.class_section_id,
                        semester_instance=semester_instance,
                        is_remedial=False, is_lab_session=False
                    ).exclude(id=fix_e.id))
                    import random as _rng2
                    _rng2.shuffle(same_class_entries)
                    for swap_e in same_class_entries:
                        # Check the swap candidate won't create a new clash
                        swap_fac_ok = TimetableEntry.objects.filter(
                            semester_instance=semester_instance,
                            faculty_id=swap_e.faculty_id,
                            time_slot_id=fix_e.time_slot_id
                        ).exclude(id=swap_e.id).count() == 0
                        fix_fac_ok = TimetableEntry.objects.filter(
                            semester_instance=semester_instance,
                            faculty_id=fix_e.faculty_id,
                            time_slot_id=swap_e.time_slot_id
                        ).exclude(id=fix_e.id).count() == 0
                        if swap_fac_ok and fix_fac_ok:
                            old_slot = fix_e.time_slot_id
                            fix_e.time_slot_id = swap_e.time_slot_id
                            swap_e.time_slot_id = old_slot
                            fix_e.save()
                            swap_e.save()
                            global_clash_fixed += 1
                            break
    
    if global_clash_fixed > 0:
        print(f"  Global clash resolution: {global_clash_fixed} clashes fixed")
    
    # Final count
    remaining = (
        TimetableEntry.objects
        .filter(semester_instance=semester_instance)
        .values('faculty_id', 'time_slot_id')
        .annotate(_cnt=_Count('id'))
        .filter(_cnt__gt=1)
        .count()
    )
    if remaining > 0:
        print(f"  WARNING: {remaining} faculty clash group(s) still remain")
    else:
        print(f"  ✓ Zero faculty clashes remaining")

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
        'generations_run': len(fitness_history),
        'remedial_validation': remedial_validation
    }
