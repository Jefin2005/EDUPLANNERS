import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eduplanner.settings')
django.setup()

from core.models import Faculty, Department


def add_faculty_for_department(dept_code, faculties):
    try:
        dept = Department.objects.get(code=dept_code)
    except Department.DoesNotExist:
        print(f"\n⚠ Department '{dept_code}' not found in database. Skipping...")
        return

    print(f"\n--- Adding faculty for {dept.name} ({dept.code}) ---")
    for name, email, designation in faculties:
        if not Faculty.objects.filter(email=email).exists():
            Faculty.objects.create(
                name=name,
                email=email,
                designation=designation,
                department=dept,
                is_active=True
            )
            print(f"  ✓ Added: {name}")
        else:
            print(f"  • Skipped (exists): {name}")


# ── Mechanical Engineering (ME) ──────────────────────────────────────────
me_faculty = [
    ('Dr. Rajesh Kumar', 'rajesh.kumar@scmsgroup.org', 'PROFESSOR'),
    ('Dr. Suresh Babu M', 'suresh.babu@scmsgroup.org', 'PROFESSOR'),

    ('Dr. Anil Kumar S', 'anil.kumar@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
    ('Dr. Priya Nair', 'priya.nair@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),

    ('Mr. Vishnu Prasad', 'vishnu.prasad@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Anjali Menon', 'anjali.menon@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Arun Thomas', 'arun.thomas@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Deepak R', 'deepak.r@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Kavitha S', 'kavitha.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Rahul Krishnan', 'rahul.krishnan@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Sanjay M', 'sanjay.m@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Nandini Raj', 'nandini.raj@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Jithin Joseph', 'jithin.joseph@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Lakshmi Priya', 'lakshmi.priya@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
]

# ── Electrical & Electronics Engineering (EEE) ──────────────────────────
eee_faculty = [
    ('Dr. Mohan Kumar P', 'mohan.kumar@scmsgroup.org', 'PROFESSOR'),
    ('Dr. Shalini R', 'shalini.r@scmsgroup.org', 'PROFESSOR'),

    ('Dr. Vineeth Thomas', 'vineeth.thomas@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
    ('Dr. Rekha M', 'rekha.m@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),

    ('Mr. Ajith Kumar V', 'ajith.kumar@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Divya Mohan', 'divya.mohan@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Sandeep K', 'sandeep.k@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Reshma Raj', 'reshma.raj@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Vivek S', 'vivek.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Aparna Nair', 'aparna.nair@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Nikhil Thomas', 'nikhil.thomas@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Sreelakshmi K', 'sreelakshmi.k@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Anand M', 'anand.m@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Gayathri S', 'gayathri.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
]

# ── Civil Engineering (CE) ───────────────────────────────────────────────
ce_faculty = [
    ('Dr. Thomas Mathew', 'thomas.mathew@scmsgroup.org', 'PROFESSOR'),
    ('Dr. Sunitha K', 'sunitha.k@scmsgroup.org', 'PROFESSOR'),

    ('Dr. Manoj Kumar T', 'manoj.kumar@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
    ('Dr. Aswathy R', 'aswathy.r@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),

    ('Mr. Akhil Raj', 'akhil.raj@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Roshni Thomas', 'roshni.thomas@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Bibin George', 'bibin.george@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Amrutha S', 'amrutha.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Nithin K', 'nithin.k@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Sneha Mohan', 'sneha.mohan@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Prasanth M', 'prasanth.m@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Devika Nair', 'devika.nair@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Kiran Kumar', 'kiran.kumar@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Arya Krishna', 'arya.krishna@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
]

# ── Automobile Engineering (AU) ──────────────────────────────────────────
au_faculty = [
    ('Dr. Saji Varghese', 'saji.varghese@scmsgroup.org', 'PROFESSOR'),

    ('Dr. Manu Joseph', 'manu.joseph@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
    ('Dr. Lekha R', 'lekha.r@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),

    ('Mr. Sachin Kumar', 'sachin.kumar@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Athira S', 'athira.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Jobin Thomas', 'jobin.thomas@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Remya R', 'remya.r@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Shabin K', 'shabin.k@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Nimisha M', 'nimisha.m@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Praveen S', 'praveen.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Gayathri Devi', 'gayathri.devi@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
]

# ── Electronics & Communication Engineering (EC) ────────────────────────
ec_faculty = [
    ('Dr. Jayakrishnan T', 'jayakrishnan.t@scmsgroup.org', 'PROFESSOR'),
    ('Dr. Sindhu S', 'sindhu.s@scmsgroup.org', 'PROFESSOR'),

    ('Dr. Rajeev M', 'rajeev.m@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
    ('Dr. Minimol K', 'minimol.k@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),

    ('Mr. Abhijith R', 'abhijith.r@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Sarika Nair', 'sarika.nair@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Dileep Kumar', 'dileep.kumar@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Krishnapriya M', 'krishnapriya.m@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Naveen Raj', 'naveen.raj@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Sumi Thomas', 'sumi.thomas@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Harish Kumar', 'harish.kumar@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Anju Mol', 'anju.mol@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Midhun S', 'midhun.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Parvathy R', 'parvathy.r@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
]

# ── Artificial Intelligence and Data Science (AIDS) ─────────────────────
aids_faculty = [
    ('Dr. Anoop V S', 'anoop.vs@scmsgroup.org', 'PROFESSOR'),

    ('Dr. Remya Krishnan', 'remya.krishnan@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
    ('Dr. Sujith Kumar', 'sujith.kumar@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),

    ('Ms. Athira Krishnan', 'athira.krishnan@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Amal Raj', 'amal.raj@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Gopika S', 'gopika.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Vishnu Mohan', 'vishnu.mohan@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Neethu Maria', 'neethu.maria@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Aswin Kumar', 'aswin.kumar@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Sreeja Roy', 'sreeja.roy@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Joel Thomas', 'joel.thomas@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Ms. Fathima Nasreen', 'fathima.nasreen@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ('Mr. Hari Prasad', 'hari.prasad@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
]


if __name__ == '__main__':
    add_faculty_for_department('ME', me_faculty)
    add_faculty_for_department('EEE', eee_faculty)
    add_faculty_for_department('CE', ce_faculty)
    add_faculty_for_department('AU', au_faculty)
    add_faculty_for_department('EC', ec_faculty)
    add_faculty_for_department('AIDS', aids_faculty)

    print("\n✅ Done! All sample faculty have been added.")
