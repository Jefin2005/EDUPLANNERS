from core.models import Faculty, FacultySubjectAssignment

dept3_faculty = Faculty.objects.filter(department_id=3).order_by('name')
print(f"--- Workload Summary for 2024-EVEN (Dept 3) ---")

for f in dept3_faculty:
    assignments = FacultySubjectAssignment.objects.filter(faculty=f, semester_instance='2024-EVEN')
    # Summing subject.hours_per_week
    # Note: hours_per_week is a property that sums L+T+P
    total_hours = sum(a.subject.hours_per_week for a in assignments)
    print(f"{f.name:30} ({f.designation:20}): {total_hours:2} hrs ({assignments.count()} subjects)")
