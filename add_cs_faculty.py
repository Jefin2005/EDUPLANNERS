from django.contrib.auth.models import User
from core.models import Faculty, Department

def add_cs_faculty():
    cs_dept = Department.objects.get(code='CS')
    
    faculties = [
        # Professors
        ('Dr. Varun G. Menon', 'varun.menon@scmsgroup.org', 'PROFESSOR'),
        ('Dr. Manish T. I', 'manish.ti@scmsgroup.org', 'PROFESSOR'),
        
        # Associate Professors
        ('Dr. Dhanya K. A', 'dhanya.ka@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
        ('Dr. Deepa K', 'deepa.k@scmsgroup.org', 'ASSOCIATE_PROFESSOR'),
        
        # Assistant Professors
        ('Ms. Josna Philomina', 'josna.philomina@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Bini Omman', 'bini.omman@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Litty Koshy', 'litty.koshy@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Neethu Krishna', 'neethu.krishna@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Surya S. G', 'surya.sg@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Greshma P. Sebastian', 'greshma.sebastian@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Doney Daniel', 'doney.daniel@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Meera V. M', 'meera.vm@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Sruthy K. Joseph', 'sruthy.joseph@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Demy Devassy', 'demy.devassy@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Vyshna R. K', 'vyshna.rk@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Mr. Anoop Jose', 'anoop.jose@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Smera Thomas', 'smera.thomas@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Dr. Noora V. T', 'noora.vt@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Sareena K. K', 'sareena.kk@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Divya M. P', 'divya.mp@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Mr. Subin P. S', 'subin.ps@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Sanooja Beegam M. A', 'sanooja.beegam@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Anisha V. Lal', 'anisha.lal@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Hafeesa M. Habeeb', 'hafeesa.habeeb@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Anu S', 'anu.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Neethu Roy', 'neethu.roy@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Supriya T. B', 'supriya.tb@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Fida Shirin', 'fida.shirin@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Sandra Vijumon', 'sandra.vijumon@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Shruthi S', 'shruthi.s@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Annamol Eldho', 'annamol.eldho@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Mr. Safeer P. S', 'safeer.ps@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Rini Joy', 'rini.joy@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Dr. Gokul G. N', 'gokul.gn@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Heera Rishikeshan', 'heera.rishikeshan@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
        ('Ms. Anugraha Raj', 'anugraha.raj@scmsgroup.org', 'ASSISTANT_PROFESSOR'),
    ]
    
    for name, email, designation in faculties:
        if not Faculty.objects.filter(email=email).exists():
            Faculty.objects.create(
                name=name,
                email=email,
                designation=designation,
                department=cs_dept,
                is_active=True
            )
            print(f"Added: {name}")
        else:
            print(f"Skipped (exists): {name}")

if __name__ == "__main__":
    add_cs_faculty()
