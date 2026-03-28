from core.models import Faculty, FacultySubjectAssignment, Subject

def check_faculty(faculty_id):
    try:
        f = Faculty.objects.get(id=faculty_id)
        print(f"\n--- Checking Faculty {faculty_id}: {f.name} ---")
        
        # Check 2024-EVEN assignments
        even_assignments = FacultySubjectAssignment.objects.filter(faculty=f, semester_instance='2024-EVEN')
        print(f"2024-EVEN Assignments: {even_assignments.count()}")
        for a in even_assignments:
            print(f" - {a.subject.code} ({a.subject.name}) in {a.class_section}")
            
        # Check 2024-ODD assignments (history)
        odd_assignments = FacultySubjectAssignment.objects.filter(faculty=f, semester_instance='2024-ODD')
        print(f"2024-ODD Assignments (Previous): {odd_assignments.count()}")
        for a in odd_assignments:
            print(f" - {a.subject.code} ({a.subject.name}) in {a.class_section}")
            
        # Check if they have ANY subjects assigned to them in the Subject model (many-to-many fallback)
        # Wait, Subject doesn't have many-to-many to Faculty directly.
            
    except Faculty.DoesNotExist:
        print(f"Faculty ID {faculty_id} not found.")

ids = [82, 97, 103, 107]
for i in ids:
    check_faculty(i)
