"""
Script to create test users for role-based authentication testing
Run this with: python create_test_users.py
"""

import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eduplanner.settings')
django.setup()

from django.contrib.auth.models import User
from core.models import UserProfile, Faculty, Department

def create_test_users():
    """Create test users for each role"""
    
    print("Creating test users for role-based authentication...")
    print("-" * 50)
    
    # 1. Create Admin User
    print("\n1. Creating Admin user...")
    admin_user, created = User.objects.get_or_create(
        username='admin_test',
        defaults={
            'email': 'admin@eduplanner.com',
            'first_name': 'Admin',
            'last_name': 'User',
            'is_staff': False,  # Not a Django staff, just EDUPLANNER admin
        }
    )
    if created:
        admin_user.set_password('admin123')
        admin_user.save()
        UserProfile.objects.create(user=admin_user, role='ADMIN')
        print(f"   [+] Created admin user: {admin_user.username}")
        print(f"     Password: admin123")
    else:
        print(f"   [*] Admin user already exists: {admin_user.username}")
    
    # 2. Create Teacher User
    print("\n2. Creating Teacher user...")
    teacher_user, created = User.objects.get_or_create(
        username='teacher_test',
        defaults={
            'email': 'teacher@eduplanner.com',
            'first_name': 'John',
            'last_name': 'Doe',
        }
    )
    if created:
        teacher_user.set_password('teacher123')
        teacher_user.save()
        UserProfile.objects.create(user=teacher_user, role='TEACHER')
        
        # Create a Faculty record for this teacher
        dept = Department.objects.first()
        if dept:
            Faculty.objects.create(
                user=teacher_user,
                name='John Doe',
                email='teacher@eduplanner.com',
                designation='ASSISTANT_PROFESSOR',
                department=dept
            )
        
        print(f"   [+] Created teacher user: {teacher_user.username}")
        print(f"     Password: teacher123")
    else:
        print(f"   [*] Teacher user already exists: {teacher_user.username}")
    
    # 3. Create Student User
    print("\n3. Creating Student user...")
    student_user, created = User.objects.get_or_create(
        username='student_test',
        defaults={
            'email': 'student@eduplanner.com',
            'first_name': 'Jane',
            'last_name': 'Smith',
        }
    )
    if created:
        student_user.set_password('student123')
        student_user.save()
        UserProfile.objects.create(user=student_user, role='STUDENT')
        print(f"   [+] Created student user: {student_user.username}")
        print(f"     Password: student123")
    else:
        print(f"   [*] Student user already exists: {student_user.username}")
    
    print("\n" + "-" * 50)
    print("Test users created successfully!")
    print("\nYou can now test login with:")
    print("  Admin:   username=admin_test, password=admin123, role=ADMIN")
    print("  Teacher: username=teacher_test, password=teacher123, role=TEACHER")
    print("  Student: username=student_test, password=student123, role=STUDENT")
    print("\nNote: Superusers can only login as ADMIN role.")

if __name__ == '__main__':
    create_test_users()
