from django.contrib.auth.models import User
from core.models import Faculty, Department

def add_bs_faculty():
    bs_dept = Department.objects.get(code='BS')
    
    faculties = [
        # Professors
        ('Dr. Mini Tom', 'minitom@scmsgroup.org', 'PROFESSOR'),
        ('Dr. Sreelekha Menon', 'sreelekha@scmsgroup.org', 'PROFESSOR'),
        
        # Associate Professors
        ('Dr. Nuja M. Unnikrishnan', 'nuja@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
        ('Dr. Geethu R', 'geethu.r@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
        ('Dr. Santhosh M. V', 'santhosh.mv@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
        ('Dr. Kannan Nithin K. V', 'kannan@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
        
        # Assistant Professors
        ('Surya K. A', 'suryaka@scmsgrp.org', 'ASSISTANT_PROFESSOR'),
        ('Reshma R', 'reshmar@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Sophia Cleetus', 'sophiyacleetus@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Preema T. S', 'preema@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Jinu M. J', 'jinu@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Rahul Ravi', 'rahul.ravi@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Mohamed Akbar V. K', 'mohamed.akbar@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Reshmi Mol N. R', 'reshmimol@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Anju Nair', 'anjunair@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Dr. Nithya Mohan M', 'nithyamohan@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Dr. Vidya Lakshmi K. P', 'vidyalakshmi@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Divya M. S', 'divyams@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Jane Theresa', 'jane@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Rony Treesa Rajesh', 'ronytrresa@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Akhil Baby', 'akhilbaby@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ]
    
    for name, email, designation in faculties:
        if not Faculty.objects.filter(email=email).exists():
            Faculty.objects.create(
                name=name,
                email=email,
                designation=designation,
                department=bs_dept,
                is_active=True
            )
            print(f"Added: {name}")
        else:
            print(f"Skipped (exists): {name}")

if __name__ == "__main__":
    add_bs_faculty()
