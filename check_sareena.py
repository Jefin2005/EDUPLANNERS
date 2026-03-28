from core.models import Faculty, FacultySubjectAssignment

f = Faculty.objects.get(name__icontains='Sareena')
assignmentsBySareena = FacultySubjectAssignment.objects.filter(faculty=f, semester_instance='2024-EVEN')
print(f"--- Checking Assignments for {f.name} ---")
for a in assignmentsBySareena:
    print(f" - {a.subject.code} ({a.subject.name}) in {a.class_section}: {a.subject.hours_per_week} hrs (ID: {a.id})")

f_hafeesa = Faculty.objects.get(id=97)
f_surya = Faculty.objects.get(id=82)
f_shruthi = Faculty.objects.get(id=103)

print(f"\nMissing Faculty Targets:")
print(f"ID 97: {f_hafeesa.name} (Assistant Professor)")
print(f"ID 82: {f_surya.name} (Assistant Professor)")
print(f"ID 103: {f_shruthi.name} (Assistant Professor)")
