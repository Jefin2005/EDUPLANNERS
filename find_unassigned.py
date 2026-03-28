from core.models import Subject, ClassSection, FacultySubjectAssignment, Semester

# Filter for EVEN semesters in Dept 3
even_sems = Semester.objects.filter(department_id=3, number__in=[2, 4, 6, 8])
sections = ClassSection.objects.filter(semester__in=even_sems)

print(f"--- Checking for Unassigned Subjects in 2024-EVEN (Dept 3) ---")

total_slots_needed = 0
assigned_slots = 0

for section in sections:
    # All subjects relevant to this section
    subjects = Subject.objects.filter(semester=section.semester)
    for subject in subjects:
        # Check if there is an assignment for this (faculty, subject, section) in 2024-EVEN
        # Wait, FacultySubjectAssignment links (faculty, subject, section)
        assignments = FacultySubjectAssignment.objects.filter(
            subject=subject,
            class_section=section,
            semester_instance='2024-EVEN'
        )
        
        if not assignments.exists():
            print(f"MISSING: {section} -> {subject.code} ({subject.name})")
        else:
            assigned_slots += 1
            
print(f"\nSummary: {assigned_slots} subjects assigned, others are missing faculty.")
