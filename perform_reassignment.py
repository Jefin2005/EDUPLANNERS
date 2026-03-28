from core.models import FacultySubjectAssignment, Faculty

updates = [
    (13590, 97, 'Ms. Hafeesa M. Habeeb'),
    (13659, 82, 'Ms. Surya S. G'),
    (13669, 103, 'Ms. Shruthi S')
]

print("--- Updating Faculty Assignments ---")
for assignment_id, target_faculty_id, target_name in updates:
    try:
        a = FacultySubjectAssignment.objects.get(id=assignment_id)
        old_faculty = a.faculty.name
        a.faculty_id = target_faculty_id
        a.save()
        print(f"ID {assignment_id}: Moved from {old_faculty} -> {target_name}")
    except Exception as e:
        print(f"ID {assignment_id}: Error - {e}")

print("\nVerifying Workloads...")
for target_id in [97, 82, 103]:
    f = Faculty.objects.get(id=target_id)
    hrs = sum(fs.subject.hours_per_week for fs in f.subject_assignments.filter(semester_instance='2024-EVEN'))
    print(f"{f.name} (ID {target_id}): {hrs} hrs allocated.")
