from django.db import models
from django.contrib.auth.models import User


class Department(models.Model):
    """Department model with fixed list of valid departments"""
    
    # Fixed list of valid department codes and names
    DEPARTMENT_CHOICES = [
        ('AU', 'Automobile Engineering'),
        ('CE', 'Civil Engineering'),
        ('CS', 'Computer Science & Engineering'),
        ('CT', 'Computer Technology'),
        ('CO', 'Computer Engineering'),
        ('EE', 'Electrical Engineering'),
        ('EC', 'Electronics & Communication Engineering'),
        ('ME', 'Mechanical Engineering'),
        ('MCA', 'Master of Computer Applications'),
    ]
    
    # Dictionary for quick lookup of department names by code
    DEPARTMENT_DICT = {code: name for code, name in DEPARTMENT_CHOICES}
    
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=10, unique=True)
    description = models.TextField(blank=True, help_text="Optional description about the department")
    is_active = models.BooleanField(default=True, help_text="Whether the department is currently active")
    
    @classmethod
    def get_department_choices(cls):
        """Returns the list of valid department choices"""
        return cls.DEPARTMENT_CHOICES
    
    @classmethod
    def is_valid_code(cls, code):
        """Validate if a department code is in the allowed list"""
        return code.upper() in cls.DEPARTMENT_DICT
    
    @classmethod
    def get_name_for_code(cls, code):
        """Get the department name for a given code"""
        return cls.DEPARTMENT_DICT.get(code.upper(), None)
    
    def __str__(self):
        return f"{self.code} - {self.name}"
    
    class Meta:
        ordering = ['code']


class Semester(models.Model):
    """Semester model with odd/even tracking"""
    number = models.IntegerField()
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='semesters')
    
    @property
    def is_odd(self):
        return self.number % 2 == 1
    
    @property
    def semester_type(self):
        return "ODD" if self.is_odd else "EVEN"
    
    def __str__(self):
        return f"S{self.number} ({self.department.code})"
    
    class Meta:
        unique_together = ['number', 'department']
        ordering = ['department', 'number']


class ClassSection(models.Model):
    """Class/Section model e.g., S5-A, S5-B"""
    name = models.CharField(max_length=20)
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='sections')
    capacity = models.IntegerField(default=60)
    
    def __str__(self):
        return f"{self.semester}-{self.name}"
    
    class Meta:
        unique_together = ['name', 'semester']
        ordering = ['semester', 'name']


class Faculty(models.Model):
    """Faculty model with workload management"""
    DESIGNATION_CHOICES = [
        ('PROFESSOR', 'Professor'),
        ('ASSOCIATE_PROFESSOR', 'Associate Professor'),
        ('ASSISTANT_PROFESSOR', 'Assistant Professor'),
    ]
    
    WORKLOAD_LIMITS = {
        'PROFESSOR': 10,
        'ASSOCIATE_PROFESSOR': 15,
        'ASSISTANT_PROFESSOR': 23,
    }
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    designation = models.CharField(max_length=30, choices=DESIGNATION_CHOICES)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='faculty_members'
    )
    preferences = models.TextField(blank=True, help_text="Comma-separated preferred subject codes")
    is_active = models.BooleanField(default=True)
    
    @property
    def max_hours(self):
        return self.WORKLOAD_LIMITS.get(self.designation, 20)
    
    @property
    def current_workload(self):
        """Calculate current assigned hours from timetable entries"""
        from django.db.models import Count
        entries = self.timetable_entries.count()
        return entries  # Each entry is 1 hour
    
    @property
    def available_hours(self):
        return self.max_hours - self.current_workload
    
    def get_preference_list(self):
        if self.preferences:
            return [p.strip() for p in self.preferences.split(',')]
        return []
    
    def __str__(self):
        return f"{self.name} ({self.get_designation_display()})"
    
    class Meta:
        verbose_name_plural = "Faculties"
        ordering = ['name']


class Subject(models.Model):
    """Subject model for theory and lab subjects - KTU B.Tech 2019 Scheme L-T-P structure"""
    SUBJECT_TYPE_CHOICES = [
        ('THEORY', 'Theory'),
        ('LAB', 'Lab'),
        ('ELECTIVE', 'Elective'),
    ]
    
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20, unique=True)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='subjects')
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='subjects')
    subject_type = models.CharField(max_length=10, choices=SUBJECT_TYPE_CHOICES)
    
    # L-T-P structure (KTU B.Tech 2019 Scheme)
    lecture_hours = models.IntegerField(default=3, help_text="Lecture hours per week (L)")
    tutorial_hours = models.IntegerField(default=0, help_text="Tutorial hours per week (T)")
    practical_hours = models.IntegerField(default=0, help_text="Practical/Lab hours per week (P)")
    
    credits = models.IntegerField(default=3)
    
    @property
    def hours_per_week(self):
        """Total hours per week (L + T + P) - backward compatible computed property"""
        return self.lecture_hours + self.tutorial_hours + self.practical_hours
    
    @property
    def ltp_string(self):
        """Returns L-T-P format string, e.g., '3-1-0'"""
        return f"{self.lecture_hours}-{self.tutorial_hours}-{self.practical_hours}"
    
    def __str__(self):
        return f"{self.code} - {self.name}"
    
    class Meta:
        ordering = ['semester', 'code']


class FacultySubjectAssignment(models.Model):
    """Track faculty-subject assignments for rotation across semesters"""
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name='subject_assignments')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='faculty_assignments')
    semester_instance = models.CharField(max_length=20, help_text="e.g., 2024-ODD, 2024-EVEN")
    is_main = models.BooleanField(default=True, help_text="For labs: main faculty or assistant")
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, null=True, blank=True)
    
    def __str__(self):
        role = "Main" if self.is_main else "Assistant"
        return f"{self.faculty.name} -> {self.subject.code} ({self.semester_instance}) [{role}]"
    
    class Meta:
        ordering = ['-semester_instance', 'faculty']


class TimeSlot(models.Model):
    """Fixed, immutable time slots for timetable infrastructure"""
    DAY_CHOICES = [
        ('MON', 'Monday'),
        ('TUE', 'Tuesday'),
        ('WED', 'Wednesday'),
        ('THU', 'Thursday'),
        ('FRI', 'Friday'),
    ]
    
    SLOT_TYPE_CHOICES = [
        ('MORNING', 'Morning Period'),
        ('AFTERNOON', 'Afternoon Period'),
        ('LUNCH', 'Lunch Break'),
        ('RECESS', 'Recess Break'),
    ]
    
    day = models.CharField(max_length=3, choices=DAY_CHOICES)
    period = models.IntegerField()  # 1-7 for teaching, 0 for lunch/recess
    start_time = models.TimeField()  # Made required (no null/blank)
    end_time = models.TimeField()    # Made required (no null/blank)
    slot_type = models.CharField(max_length=10, choices=SLOT_TYPE_CHOICES, default='MORNING')
    is_locked = models.BooleanField(default=True, help_text="Prevent modifications to slot")
    
    @property
    def is_morning(self):
        return self.slot_type == 'MORNING'
    
    @property
    def is_afternoon(self):
        return self.slot_type == 'AFTERNOON'
    
    @property
    def is_teaching_slot(self):
        """Only morning/afternoon slots can be used for teaching"""
        return self.slot_type in ['MORNING', 'AFTERNOON']
    
    @property
    def slot_name(self):
        return f"{self.get_day_display()} Period {self.period}"
    
    @property
    def duration_minutes(self):
        """Calculate slot duration in minutes"""
        from datetime import datetime
        start = datetime.combine(datetime.today(), self.start_time)
        end = datetime.combine(datetime.today(), self.end_time)
        return int((end - start).total_seconds() / 60)
    
    def __str__(self):
        if self.slot_type == 'LUNCH':
            return f"{self.day}-LUNCH"
        return f"{self.day}-P{self.period}"
    
    def save(self, *args, **kwargs):
        """Prevent modifications to locked slots"""
        if self.pk and self.is_locked:
            # Check if this is just unlocking the slot
            old = TimeSlot.objects.get(pk=self.pk)
            # Allow only is_locked field changes
            if (old.day != self.day or old.period != self.period or 
                old.start_time != self.start_time or old.end_time != self.end_time or
                old.slot_type != self.slot_type):
                raise ValueError("Cannot modify locked time slot. Unlock it first.")
        super().save(*args, **kwargs)
    
    class Meta:
        unique_together = ['day', 'period']
        ordering = ['day', 'period']


class TimetableEntry(models.Model):
    """Individual timetable entry linking class, subject, faculty, and time"""
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name='timetable_entries')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='timetable_entries')
    faculty = models.ForeignKey(Faculty, on_delete=models.CASCADE, related_name='timetable_entries')
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, related_name='timetable_entries')
    semester_instance = models.CharField(max_length=20, help_text="e.g., 2024-ODD")
    room = models.CharField(max_length=20, blank=True)
    
    # For labs: track assistant faculty
    assistant_faculty = models.ForeignKey(
        Faculty, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='assistant_entries'
    )
    is_lab_session = models.BooleanField(default=False)
    lab_session_number = models.IntegerField(null=True, blank=True, help_text="1 or 2 for weekly lab sessions")
    
    def __str__(self):
        return f"{self.class_section} | {self.time_slot} | {self.subject.code} | {self.faculty.name}"
    
    class Meta:
        ordering = ['class_section', 'time_slot']


class SystemConfiguration(models.Model):
    """System-wide configuration"""
    SEMESTER_TYPE_CHOICES = [
        ('ODD', 'Odd Semesters (1, 3, 5, 7)'),
        ('EVEN', 'Even Semesters (2, 4, 6, 8)'),
    ]
    
    active_semester_type = models.CharField(max_length=4, choices=SEMESTER_TYPE_CHOICES, default='ODD')
    current_academic_year = models.CharField(max_length=20, default='2024-25')
    periods_per_day = models.IntegerField(default=7)
    days_per_week = models.IntegerField(default=5)
    
    def get_semester_instance(self):
        year = self.current_academic_year.split('-')[0]
        return f"{year}-{self.active_semester_type}"
    
    def __str__(self):
        return f"Config: {self.current_academic_year} - {self.active_semester_type}"
    
    class Meta:
        verbose_name = "System Configuration"
        verbose_name_plural = "System Configuration"
    
    def save(self, *args, **kwargs):
        # Ensure only one configuration exists
        if not self.pk and SystemConfiguration.objects.exists():
            raise ValueError("Only one SystemConfiguration instance is allowed")
        super().save(*args, **kwargs)
