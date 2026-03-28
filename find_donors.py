from core.models import Faculty, FacultySubjectAssignment

dept3_faculty = Faculty.objects.filter(department_id=3)
print(f"--- Detailed Assignments for Dept 3 (2024-EVEN) ---")

for f in dept3_faculty:
    assignments = FacultySubjectAssignment.objects.filter(faculty=f, semester_instance='2024-EVEN')
    total_hours = sum(a.subject.hours_per_week for a in assignments)
    if total_hours > 10:
        print(f"\n{f.name} ({total_hours} hrs):")
        for a in assignments:
            print(f"  - {a.id}: {a.subject.code} ({a.subject.name}) in {a.class_section} [{a.subject.hours_per_week} hrs]")
