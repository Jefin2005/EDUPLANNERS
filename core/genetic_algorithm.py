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
        'lab_continuity': -500,      # Hard: Lab not in 3 continuous periods
        'lab_timing': -100,          # Soft: Labs should be morning OR afternoon
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
        
    def load_data(self, classes, subjects, faculties, time_slots, 
                  faculty_preferences=None, faculty_history=None):
        """Load problem data from Django models"""
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
        
        for class_info in self.classes:
            class_id = class_info['id']
            class_subject_ids = self.class_subjects[class_id]
            
            # Get available time slots
            available_slots = [ts['id'] for ts in self.time_slots]
            used_slots = set()
            
            # First, schedule labs (need 3 continuous periods each, 2 per week)
            lab_subjects_for_class = [
                s_id for s_id in class_subject_ids 
                if self.subject_info[s_id]['subject_type'] == 'LAB'
            ]
            
            for lab_id in lab_subjects_for_class[:2]:  # Max 2 labs per week
                # Find 3 continuous morning or afternoon slots
                lab_slots = self._find_lab_slots(available_slots, used_slots)
                if lab_slots:
                    # Assign main and assistant faculty
                    eligible_faculty = self._get_eligible_faculty_for_subject(lab_id)
                    if len(eligible_faculty) >= 2:
                        main_faculty = random.choice(eligible_faculty)
                        assistant_faculty = random.choice([f for f in eligible_faculty if f != main_faculty])
                    elif len(eligible_faculty) == 1:
                        main_faculty = eligible_faculty[0]
                        assistant_faculty = None
                    else:
                        main_faculty = random.choice([f['id'] for f in self.faculties])
                        assistant_faculty = None
                    
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
            
            # Then, schedule theory subjects
            theory_subjects_for_class = [
                s_id for s_id in class_subject_ids 
                if self.subject_info[s_id]['subject_type'] == 'THEORY'
            ]
            
            for subject_id in theory_subjects_for_class:
                hours_needed = self.subject_info[subject_id].get('hours_per_week', 3)
                eligible_faculty = self._get_eligible_faculty_for_subject(subject_id)
                
                if eligible_faculty:
                    faculty_id = random.choice(eligible_faculty)
                else:
                    faculty_id = random.choice([f['id'] for f in self.faculties])
                
                # Assign hours across the week
                slots_assigned = 0
                remaining_slots = [s for s in available_slots if s not in used_slots]
                random.shuffle(remaining_slots)
                
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
                    slots_assigned += 1
        
        return Chromosome(genes=genes)
    
    def _find_lab_slots(self, available_slots: List[int], used_slots: set) -> List[int]:
        """Find 3 continuous periods for a lab session"""
        # Group slots by day
        slots_by_day = defaultdict(list)
        for slot in self.time_slots:
            if slot['id'] in available_slots and slot['id'] not in used_slots:
                slots_by_day[slot['day']].append(slot)
        
        # Look for 3 continuous morning (1-3) or afternoon (5-7) slots
        for day, day_slots in slots_by_day.items():
            day_slots.sort(key=lambda x: x['period'])
            
            # Try morning slots (periods 1, 2, 3)
            morning_slots = [s for s in day_slots if s['period'] <= 3]
            if len(morning_slots) >= 3:
                periods = [s['period'] for s in morning_slots]
                if 1 in periods and 2 in periods and 3 in periods:
                    return [s['id'] for s in morning_slots if s['period'] <= 3][:3]
            
            # Try afternoon slots (periods 5, 6, 7)
            afternoon_slots = [s for s in day_slots if s['period'] >= 5]
            if len(afternoon_slots) >= 3:
                periods = [s['period'] for s in afternoon_slots]
                if 5 in periods and 6 in periods and 7 in periods:
                    return [s['id'] for s in afternoon_slots if s['period'] >= 5][:3]
        
        # Fallback: return any 3 continuous slots
        for day, day_slots in slots_by_day.items():
            if len(day_slots) >= 3:
                day_slots.sort(key=lambda x: x['period'])
                for i in range(len(day_slots) - 2):
                    if day_slots[i+1]['period'] == day_slots[i]['period'] + 1 and \
                       day_slots[i+2]['period'] == day_slots[i]['period'] + 2:
                        return [day_slots[i]['id'], day_slots[i+1]['id'], day_slots[i+2]['id']]
        
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
        
        # Check workload limits
        for faculty_id, hours in faculty_hours.items():
            max_hours = self.faculty_workload_limits.get(faculty_id, 20)
            if hours > max_hours:
                fitness += self.WEIGHTS['workload_exceeded'] * (hours - max_hours)
        
        # Check lab constraints
        for class_id, lab_genes in class_labs.items():
            # Group by subject to check for 2 lab sessions
            lab_subjects_scheduled = set(g.subject_id for g in lab_genes)
            
            for lab_subject_id in lab_subjects_scheduled:
                subject_lab_genes = [g for g in lab_genes if g.subject_id == lab_subject_id]
                
                # Check lab continuity
                slot_ids = [g.time_slot_id for g in subject_lab_genes]
                if not self._check_lab_continuity(slot_ids):
                    fitness += self.WEIGHTS['lab_continuity']
                
                # Check lab timing (should be all morning or all afternoon)
                if not self._check_lab_timing(slot_ids):
                    fitness += self.WEIGHTS['lab_timing']
        
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
            # Swap time slots between two genes of the same class
            gene1 = random.choice(mutated.genes)
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
    
    # Get all active faculty (department-wide or unassigned)
    faculties = list(Faculty.objects.filter(
        is_active=True
    ).filter(
        Q(department_id=department_id) | Q(department_id__isnull=True)
    ).values('id', 'name', 'designation', 'preferences'))
    
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
        faculty_history=dict(faculty_history)
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
