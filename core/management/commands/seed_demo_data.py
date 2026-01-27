"""
Django management command to seed demo data for EDUPLANNERS
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import (
    Department, Semester, ClassSection, Faculty, Subject,
    SystemConfiguration
)
from core.views import _create_time_slots
from datetime import time


class Command(BaseCommand):
    help = 'Seed demo data for EDUPLANNERS system'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING('🌱 Seeding demo data...'))
        
        # Create system configuration
        self.create_system_config()
        
        # Create departments
        departments = self.create_departments()
        
        # Create semesters and classes
        semesters = self.create_semesters_and_classes(departments)
        
        # Create faculty
        faculty = self.create_faculty(departments)
        
        # Create subjects
        self.create_subjects(semesters)
        
        # Initialize time slots
        self.initialize_time_slots()
        
        self.stdout.write(self.style.SUCCESS('✅ Demo data seeded successfully!'))
        self.stdout.write(self.style.SUCCESS('📊 Summary:'))
        self.stdout.write(f'  - Departments: {Department.objects.count()}')
        self.stdout.write(f'  - Semesters: {Semester.objects.count()}')
        self.stdout.write(f'  - Classes: {ClassSection.objects.count()}')
        self.stdout.write(f'  - Faculty: {Faculty.objects.count()}')
        self.stdout.write(f'  - Subjects: {Subject.objects.count()}')
        self.stdout.write(f'  - Time Slots: 40 (35 teaching + 5 lunch)')

    def create_system_config(self):
        """Create system configuration"""
        if not SystemConfiguration.objects.exists():
            SystemConfiguration.objects.create(
                active_semester_type='EVEN',
                current_academic_year='2024-25',
                periods_per_day=7,
                days_per_week=5
            )
            self.stdout.write('  ✓ System configuration created')
        else:
            self.stdout.write('  ℹ System configuration already exists')

    def create_departments(self):
        """Create demo departments"""
        departments_data = [
            ('CS', 'Computer Science & Engineering'),
            ('EC', 'Electronics & Communication Engineering'),
            ('ME', 'Mechanical Engineering'),
        ]
        
        departments = []
        for code, name in departments_data:
            dept, created = Department.objects.get_or_create(
                code=code,
                defaults={'name': name, 'is_active': True}
            )
            departments.append(dept)
            if created:
                self.stdout.write(f'  ✓ Created department: {code}')
        
        return departments

    def create_semesters_and_classes(self, departments):
        """Create semesters and classes for departments"""
        semesters = []
        
        for dept in departments:
            # Create even semesters (2, 4, 6, 8)
            for sem_num in [2, 4, 6, 8]:
                semester, created = Semester.objects.get_or_create(
                    number=sem_num,
                    department=dept
                )
                semesters.append(semester)
                
                if created:
                    self.stdout.write(f'  ✓ Created semester: S{sem_num} for {dept.code}')
                
                # Create 4 classes per semester
                for class_name in ['A', 'B', 'C', 'D']:
                    ClassSection.objects.get_or_create(
                        name=class_name,
                        semester=semester,
                        defaults={'capacity': 60}
                    )
        
        self.stdout.write(f'  ✓ Created {ClassSection.objects.count()} classes')
        return semesters

    def create_faculty(self, departments):
        """Create demo faculty members"""
        faculty_data = [
            # CS Department
            ('Dr. Rajesh Kumar', 'rajesh@example.com', 'PROFESSOR', 'CS', 'CST201,CST203,CST205'),
            ('Dr. Priya Sharma', 'priya@example.com', 'ASSOCIATE_PROFESSOR', 'CS', 'CST207,CST209'),
            ('Mr. Amit Verma', 'amit@example.com', 'ASSISTANT_PROFESSOR', 'CS', 'CST211,CST213'),
            ('Ms. Sneha Reddy', 'sneha@example.com', 'ASSISTANT_PROFESSOR', 'CS', 'CST215,CST217'),
            
            # EC Department
            ('Dr. Suresh Nair', 'suresh@example.com', 'PROFESSOR', 'EC', 'ECT201,ECT203'),
            ('Dr. Lakshmi Iyer', 'lakshmi@example.com', 'ASSOCIATE_PROFESSOR', 'EC', 'ECT205,ECT207'),
            ('Mr. Karthik Menon', 'karthik@example.com', 'ASSISTANT_PROFESSOR', 'EC', 'ECT209'),
            
            # ME Department
            ('Dr. Vijay Singh', 'vijay@example.com', 'PROFESSOR', 'ME', 'MET201,MET203'),
            ('Dr. Meera Patel', 'meera@example.com', 'ASSOCIATE_PROFESSOR', 'ME', 'MET205'),
            ('Mr. Ravi Kumar', 'ravi@example.com', 'ASSISTANT_PROFESSOR', 'ME', 'MET207'),
        ]
        
        faculty_list = []
        dept_dict = {d.code: d for d in departments}
        
        for name, email, designation, dept_code, preferences in faculty_data:
            # Create user account
            username = email.split('@')[0]
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={'email': email}
            )
            
            faculty_obj, created = Faculty.objects.get_or_create(
                email=email,
                defaults={
                    'user': user,
                    'name': name,
                    'designation': designation,
                    'department': dept_dict.get(dept_code),
                    'preferences': preferences,
                    'is_active': True
                }
            )
            faculty_list.append(faculty_obj)
            if created:
                self.stdout.write(f'  ✓ Created faculty: {name}')
        
        return faculty_list

    def create_subjects(self, semesters):
        """Create demo subjects for semesters"""
        # Template: (name, code_prefix, type, L, T, P, credits)
        subjects_template = [
            # Theory subjects (L-T-P format)
            ('Data Structures', 'DST', 'THEORY', 3, 1, 0, 4),
            ('Database Management', 'DBM', 'THEORY', 3, 0, 0, 3),
            ('Computer Networks', 'CNW', 'THEORY', 3, 1, 0, 4),
            ('Operating Systems', 'OST', 'THEORY', 3, 0, 0, 3),
            
            # Lab subjects
            ('Data Structures Lab', 'DSL', 'LAB', 0, 0, 3, 2),
            ('DBMS Lab', 'DBL', 'LAB', 0, 0, 3, 2),
        ]
        
        # Create subjects for CS department semesters
        cs_semesters = [s for s in semesters if s.department.code == 'CS']
        
        for semester in cs_semesters:
            for idx, (name, code_prefix, sub_type, L, T, P, credits) in enumerate(subjects_template, 1):
                # Create unique code: CS201, CS202, etc.
                unique_code = f'{semester.department.code}{semester.number}{idx:02d}'
                full_name = f'{name} - S{semester.number}'
                
                Subject.objects.get_or_create(
                    code=unique_code,
                    defaults={
                        'name': full_name,
                        'department': semester.department,
                        'semester': semester,
                        'subject_type': sub_type,
                        'lecture_hours': L,
                        'tutorial_hours': T,
                        'practical_hours': P,
                        'credits': credits
                    }
                )
        
        self.stdout.write(f'  ✓ Created {Subject.objects.count()} subjects')

    def initialize_time_slots(self):
        """Initialize time slots"""
        from core.models import TimeSlot
        
        if TimeSlot.objects.exists():
            self.stdout.write('  ℹ Time slots already exist')
            return
        
        _create_time_slots()
        self.stdout.write('  ✓ Initialized 40 time slots (35 teaching + 5 lunch)')
