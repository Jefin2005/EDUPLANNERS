from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.db.models import Count, Q
from collections import defaultdict
import json

from .models import (
    Department, Semester, ClassSection, Faculty, Subject,
    FacultySubjectAssignment, TimeSlot, TimetableEntry, SystemConfiguration,
    StudentProfile
)
from .genetic_algorithm import generate_timetable


def home(request):
    """Homepage with navigation"""
    config = SystemConfiguration.objects.first()
    context = {
        'config': config,
    }
    return render(request, 'home.html', context)


# ============ AUTHENTICATION ============

def login_view(request):
    """Login page with role-based authentication"""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        role = request.POST.get('role')  # Get selected role from login form
        
        # Validate that role was selected
        if not role:
            messages.error(request, 'Please select a role to login.')
            return render(request, 'login.html')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            # Handle superuser login
            if user.is_superuser:
                # Superuser can only login as ADMIN
                if role != 'ADMIN':
                    messages.error(request, 'Superuser accounts can only login as Admin.')
                    return render(request, 'login.html')
                login(request, user)
                return redirect('admin_dashboard')
            
            # Check if user has profile
            if not hasattr(user, 'profile'):
                messages.error(request, 'User profile not found. Please contact administrator.')
                return render(request, 'login.html')
            
            # Verify selected role matches user's actual role
            if user.profile.role != role:
                role_display = dict(user.profile.ROLE_CHOICES).get(user.profile.role, 'Unknown')
                messages.error(request, f'Invalid role selection. You are registered as {role_display}.')
                return render(request, 'login.html')
            
            # Login successful - redirect based on role
            login(request, user)
            
            if role == 'ADMIN':
                return redirect('admin_dashboard')
            elif role == 'TEACHER':
                return redirect('teacher_dashboard')
            elif role == 'STUDENT':
                return redirect('student_dashboard')
            else:
                messages.error(request, 'Invalid role.')
                return render(request, 'login.html')
        else:
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'login.html')



def logout_view(request):
    """Logout user"""
    logout(request)
    return redirect('home')


from .decorators import role_required

# ============ ADMIN DASHBOARD ============

@role_required('ADMIN')
def admin_dashboard(request):
    """Main admin dashboard"""
    config = SystemConfiguration.objects.first()
    if not config:
        config = SystemConfiguration.objects.create()
    
    departments = Department.objects.all()
    total_faculty = Faculty.objects.filter(is_active=True).count()
    total_subjects = Subject.objects.count()
    total_classes = ClassSection.objects.count()
    
    # Get active semesters based on ODD/EVEN mode
    if config.active_semester_type == 'ODD':
        active_semesters = Semester.objects.filter(number__in=[1, 3, 5, 7])
    else:
        active_semesters = Semester.objects.filter(number__in=[2, 4, 6, 8])
    
    context = {
        'config': config,
        'departments': departments,
        'total_faculty': total_faculty,
        'total_subjects': total_subjects,
        'total_classes': total_classes,
        'active_semesters': active_semesters,
    }
    return render(request, 'admin/dashboard.html', context)


@role_required('ADMIN')
def manage_departments(request):
    """Manage departments"""
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add':
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip().upper()
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active', '1') == '1'
            Department.objects.create(
                name=name, 
                code=code,
                description=description,
                is_active=is_active
            )
            messages.success(request, f'Department {code} created successfully.')
        
        elif action == 'update':
            dept_id = request.POST.get('department_id')
            dept = Department.objects.get(id=dept_id)
            dept.name = request.POST.get('name', '').strip()
            dept.code = request.POST.get('code', '').strip().upper()
            dept.description = request.POST.get('description', '').strip()
            dept.is_active = request.POST.get('is_active', '1') == '1'
            dept.save()
            messages.success(request, f'Department {dept.code} updated successfully.')
        
        elif action == 'delete':
            dept_id = request.POST.get('department_id')
            dept = Department.objects.filter(id=dept_id).first()
            if dept:
                messages.success(request, f'Department {dept.code} deleted successfully.')
                dept.delete()
    
    departments = Department.objects.annotate(
        semester_count=Count('semesters'),
        subject_count=Count('subjects')
    )
    
    active_count = departments.filter(is_active=True).count()
    
    return render(request, 'admin/departments.html', {
        'departments': departments,
        'active_count': active_count
    })


def get_department_choices(request):
    """API endpoint to get the list of valid department choices"""
    choices = Department.get_department_choices()
    return JsonResponse({
        'departments': [
            {'code': code, 'name': name}
            for code, name in choices
        ]
    })


@role_required('ADMIN')
def add_department(request):
    """Add or edit a department - dedicated page with fixed department selection"""
    
    errors = {}
    form_data = {}
    department = None
    
    # Get the list of valid department choices
    department_choices = Department.get_department_choices()
    
    # Check if editing an existing department
    edit_id = request.GET.get('edit')
    if edit_id:
        department = Department.objects.filter(id=edit_id).first()
    
    if request.method == 'POST':
        action = request.POST.get('action')
        code = request.POST.get('code', '').strip().upper()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active', '1') == '1'
        
        # Auto-populate name from the code using the fixed choices
        name = Department.get_name_for_code(code)
        
        form_data = {
            'code': code,
            'description': description,
            'is_active': '1' if is_active else '0'
        }
        
        # Validation
        if not code:
            errors['code'] = 'Please select a department'
        elif not Department.is_valid_code(code):
            errors['code'] = 'Invalid department selected. Please choose from the list.'
        else:
            # Check for duplicate code (excluding current department if editing)
            existing = Department.objects.filter(code=code)
            if action == 'update':
                dept_id = request.POST.get('department_id')
                existing = existing.exclude(id=dept_id)
            if existing.exists():
                errors['code'] = 'This department has already been added'
        
        if not errors:
            if action == 'update':
                # Update existing department
                dept_id = request.POST.get('department_id')
                dept = Department.objects.get(id=dept_id)
                dept.name = name
                dept.code = code
                dept.description = description
                dept.is_active = is_active
                dept.save()
                messages.success(request, f'Department "{code} - {name}" updated successfully!')
            else:
                # Create new department
                Department.objects.create(
                    name=name,
                    code=code,
                    description=description,
                    is_active=is_active
                )
                messages.success(request, f'Department "{code} - {name}" created successfully!')
            return redirect('manage_departments')
    
    return render(request, 'admin/add_department.html', {
        'errors': errors,
        'form_data': form_data,
        'department': department,
        'department_choices': department_choices
    })


@role_required('ADMIN')
def manage_semesters(request):
    """Manage semesters and classes"""
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add_semester':
            dept_id = request.POST.get('department_id')
            number = request.POST.get('number')
            Semester.objects.create(department_id=dept_id, number=number)
            messages.success(request, f'Semester S{number} created.')
        
        elif action == 'add_class':
            semester_id = request.POST.get('semester_id')
            name = request.POST.get('name')
            capacity = request.POST.get('capacity', 60)
            ClassSection.objects.create(
                semester_id=semester_id, 
                name=name, 
                capacity=capacity
            )
            messages.success(request, f'Class {name} created.')
        
        elif action == 'delete_semester':
            semester_id = request.POST.get('semester_id')
            Semester.objects.filter(id=semester_id).delete()
            messages.success(request, 'Semester deleted.')
        
        elif action == 'delete_class':
            class_id = request.POST.get('class_id')
            ClassSection.objects.filter(id=class_id).delete()
            messages.success(request, 'Class deleted.')
    
    departments = Department.objects.prefetch_related('semesters__sections')
    
    return render(request, 'admin/semesters.html', {'departments': departments})


@role_required('ADMIN')
def add_semester(request):
    """Add a new semester with classes - dedicated page"""
    from django.db import transaction
    
    errors = {}
    form_data = {}
    classes_data = []
    
    if request.method == 'POST':
        dept_id = request.POST.get('department_id', '').strip()
        number = request.POST.get('number', '').strip()
        academic_year = request.POST.get('academic_year', '').strip()
        is_active = request.POST.get('is_active', '1') == '1'
        
        form_data = {
            'department_id': dept_id,
            'number': number,
            'academic_year': academic_year,
            'is_active': '1' if is_active else '0'
        }
        
        # Collect class data from form
        class_names = request.POST.getlist('class_name[]')
        class_sections = request.POST.getlist('class_section[]')
        class_statuses = request.POST.getlist('class_status[]')
        
        # Build classes data list for template re-rendering
        for i in range(len(class_names)):
            classes_data.append({
                'name': class_names[i] if i < len(class_names) else '',
                'section': class_sections[i] if i < len(class_sections) else '',
                'status': class_statuses[i] if i < len(class_statuses) else '1'
            })
        
        # Validation
        if not dept_id:
            errors['department'] = 'Please select a department'
        
        if not number:
            errors['number'] = 'Please select a semester'
        
        if not academic_year:
            errors['academic_year'] = 'Academic year is required'
        
        # Validate classes - at least check that provided class names are not empty
        class_errors = []
        for i, cls in enumerate(classes_data):
            if cls['name'].strip():
                class_errors.append(None)
            else:
                # Only flag error if it's not the only empty row
                if len(classes_data) > 1 or any(c['name'].strip() for c in classes_data):
                    class_errors.append('Class name is required')
                else:
                    class_errors.append(None)
        
        if any(class_errors):
            errors['classes'] = class_errors
        
        if dept_id and number and not errors:
            # Check if semester already exists
            existing = Semester.objects.filter(department_id=dept_id, number=number).exists()
            if existing:
                errors['number'] = f'Semester {number} already exists for this department'
            else:
                try:
                    with transaction.atomic():
                        # Create the semester
                        semester = Semester.objects.create(department_id=dept_id, number=number)
                        
                        # Create all classes
                        classes_created = 0
                        for cls in classes_data:
                            class_name = cls['name'].strip()
                            if class_name:
                                # Combine name and section if section is provided
                                section = cls['section'].strip()
                                full_name = f"{class_name} {section}".strip() if section else class_name
                                
                                ClassSection.objects.create(
                                    semester=semester,
                                    name=full_name,
                                    capacity=60  # Default capacity
                                )
                                classes_created += 1
                        
                        if classes_created > 0:
                            messages.success(request, f'Semester S{number} created with {classes_created} class(es).')
                        else:
                            messages.success(request, f'Semester S{number} created successfully.')
                        
                        return redirect('manage_semesters')
                        
                except Exception as e:
                    errors['general'] = f'An error occurred: {str(e)}'
    
    departments = Department.objects.filter(is_active=True)
    selected_dept = request.GET.get('department')
    
    # Initialize with one empty class row if no classes data
    if not classes_data:
        classes_data = [{'name': '', 'section': '', 'status': '1'}]
    
    return render(request, 'admin/add_semester.html', {
        'departments': departments,
        'selected_dept': int(selected_dept) if selected_dept else None,
        'errors': errors,
        'form_data': form_data,
        'classes_data': classes_data
    })


@role_required('ADMIN')
def add_class(request):
    """Add a new class section - dedicated page"""
    
    if request.method == 'POST':
        semester_id = request.POST.get('semester_id')
        name = request.POST.get('name', '').strip()
        capacity = request.POST.get('capacity', 60)
        
        if semester_id and name:
            # Check if class already exists
            existing = ClassSection.objects.filter(semester_id=semester_id, name=name).exists()
            if existing:
                messages.error(request, f'Class {name} already exists for this semester.')
            else:
                ClassSection.objects.create(
                    semester_id=semester_id,
                    name=name,
                    capacity=capacity or 60
                )
                messages.success(request, f'Class {name} created successfully.')
                return redirect('manage_semesters')
    
    semesters = Semester.objects.select_related('department').order_by('department__code', 'number')
    selected_semester = request.GET.get('semester')
    
    return render(request, 'admin/add_class.html', {
        'semesters': semesters,
        'selected_semester': int(selected_semester) if selected_semester else None
    })


@role_required('ADMIN')
def manage_faculty(request):
    """Manage faculty members"""
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add':
            department_id = request.POST.get('department_id')
            Faculty.objects.create(
                name=request.POST.get('name'),
                email=request.POST.get('email'),
                designation=request.POST.get('designation'),
                department_id=department_id if department_id else None,
                preferences=request.POST.get('preferences', '')
            )
            messages.success(request, 'Faculty added successfully.')
        
        elif action == 'update':
            faculty_id = request.POST.get('faculty_id')
            faculty = Faculty.objects.get(id=faculty_id)
            faculty.name = request.POST.get('name')
            faculty.email = request.POST.get('email')
            faculty.designation = request.POST.get('designation')
            department_id = request.POST.get('department_id')
            faculty.department_id = department_id if department_id else None
            faculty.preferences = request.POST.get('preferences', '')
            faculty.save()
            messages.success(request, 'Faculty updated successfully.')
        
        elif action == 'delete':
            faculty_id = request.POST.get('faculty_id')
            Faculty.objects.filter(id=faculty_id).delete()
            messages.success(request, 'Faculty deleted.')
        
        elif action == 'toggle_active':
            faculty_id = request.POST.get('faculty_id')
            faculty = Faculty.objects.get(id=faculty_id)
            faculty.is_active = not faculty.is_active
            faculty.save()
    
    # Pre-compute all display data for template (no comparisons in template)
    designation_labels = {
        'PROFESSOR': 'Professor',
        'ASSOCIATE_PROFESSOR': 'Associate Professor',
        'ASSISTANT_PROFESSOR': 'Assistant Professor',
    }
    
    # Fetch departments for dropdown
    departments = list(Department.objects.filter(is_active=True).order_by('code'))
    
    # Pre-build designation options
    designation_options = [
        {'value': 'PROFESSOR', 'label': 'Professor'},
        {'value': 'ASSOCIATE_PROFESSOR', 'label': 'Associate Professor'},
        {'value': 'ASSISTANT_PROFESSOR', 'label': 'Assistant Professor'},
    ]
    
    # Group faculty by department
    from collections import OrderedDict
    faculty_by_dept = OrderedDict()
    
    # Initialize with all departments
    for dept in departments:
        faculty_by_dept[dept.code] = {
            'department_name': dept.name,
            'department_code': dept.code,
            'department_id': dept.id,
            'faculty_list': []
        }
    
    # Add unassigned category
    faculty_by_dept['UNASSIGNED'] = {
        'department_name': 'Unassigned',
        'department_code': 'UNASSIGNED',
        'department_id': None,
        'faculty_list': []
    }
    
    # Flat list for modals
    faculty_list = []
    
    for f in Faculty.objects.select_related('department').order_by('department__code', 'designation', 'name'):
        # Build designation options with selected flag
        desig_options = [
            {'value': opt['value'], 'label': opt['label'], 'selected': f.designation == opt['value']}
            for opt in designation_options
        ]
        
        # Build department options with selected flag for this faculty
        dept_options = []
        for dept in departments:
            dept_options.append({
                'id': dept.id,
                'name': dept.name,
                'code': dept.code,
                'selected': f.department_id == dept.id if f.department_id else False
            })
        
        faculty_data = {
            'id': f.id,
            'name': f.name,
            'email': f.email,
            'designation_display': designation_labels.get(f.designation, f.designation),
            'department_display': f.department.name if f.department else 'Unassigned',
            'department_code': f.department.code if f.department else 'UNASSIGNED',
            'status_display': 'Active' if f.is_active else 'Inactive',
            'is_active': f.is_active,
            'desig_options': desig_options,
            'dept_options': dept_options,
        }
        
        # Add to grouped structure
        dept_key = f.department.code if f.department else 'UNASSIGNED'
        if dept_key in faculty_by_dept:
            faculty_by_dept[dept_key]['faculty_list'].append(faculty_data)
        
        # Also add to flat list for modals
        faculty_list.append(faculty_data)
    
    # Remove empty departments from display
    faculty_by_dept = {k: v for k, v in faculty_by_dept.items() if v['faculty_list']}
    
    return render(request, 'admin/faculty.html', {
        'faculty_list': faculty_list,
        'faculty_by_dept': faculty_by_dept,
        'departments': departments
    })


@role_required('ADMIN')
def manage_subjects(request):
    """Manage subjects with department and semester filtering"""
    
    # Handle delete action
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            subject_id = request.POST.get('subject_id')
            subject = Subject.objects.filter(id=subject_id).first()
            if subject:
                messages.success(request, f'Subject "{subject.code}" deleted successfully.')
                subject.delete()
            return redirect('manage_subjects')
    
    # Get filter parameters
    selected_dept = request.GET.get('department', '')
    selected_sem = request.GET.get('semester', '')
    
    # Build department options with selected attribute (for template)
    departments = Department.objects.filter(is_active=True).order_by('code')
    department_options = []
    for dept in departments:
        department_options.append({
            'code': dept.code,
            'name': dept.name,
            'selected': 'selected' if selected_dept == dept.code else ''
        })
    
    # Build semester options with selected attribute
    semester_options = []
    for i in range(1, 9):
        semester_options.append({
            'value': str(i),
            'selected': 'selected' if selected_sem == str(i) else ''
        })
    
    # Build subjects query
    subjects = Subject.objects.select_related('department', 'semester')
    
    if selected_dept:
        subjects = subjects.filter(department__code=selected_dept)
    if selected_sem:
        subjects = subjects.filter(semester__number=selected_sem)
    
    subjects = subjects.order_by('department__code', 'semester__number', 'code')
    
    # Type badge mapping - using custom EDUPLANNER theme classes
    type_badges = {
        'THEORY': 'badge-theory',
        'LAB': 'badge-lab',
        'ELECTIVE': 'badge-elective'
    }
    type_displays = {
        'THEORY': 'Theory',
        'LAB': 'Lab',
        'ELECTIVE': 'Elective'
    }
    
    # Group subjects by department and semester
    grouped_subjects = {}
    for subject in subjects:
        dept_code = subject.department.code
        dept_id = subject.department.id
        sem_num = subject.semester.number
        sem_id = subject.semester.id

        if dept_code not in grouped_subjects:
            grouped_subjects[dept_code] = {
                'name': subject.department.name,
                'id': dept_id,
                'semesters': {}
            }

        if sem_num not in grouped_subjects[dept_code]['semesters']:
            grouped_subjects[dept_code]['semesters'][sem_num] = {
                'subjects': [],
                'id': sem_id
            }
        
        # Add subject with display info
        grouped_subjects[dept_code]['semesters'][sem_num]['subjects'].append({
            'id': subject.id,
            'code': subject.code,
            'name': subject.name,
            'ltp_string': subject.ltp_string,
            'hours_per_week': subject.hours_per_week,
            'credits': subject.credits,
            'type_badge': type_badges.get(subject.subject_type, 'bg-secondary'),
            'type_display': type_displays.get(subject.subject_type, subject.subject_type)
        })
    
    # Get counts
    total_subjects = Subject.objects.count()
    theory_count = Subject.objects.filter(subject_type='THEORY').count()
    lab_count = Subject.objects.filter(subject_type='LAB').count()
    elective_count = Subject.objects.filter(subject_type='ELECTIVE').count()
    
    return render(request, 'admin/subjects.html', {
        'grouped_subjects': grouped_subjects,
        'department_options': department_options,
        'semester_options': semester_options,
        'total_subjects': total_subjects,
        'theory_count': theory_count,
        'lab_count': lab_count,
        'elective_count': elective_count,
    })


@role_required('ADMIN')
def add_subject(request):
    """Add a new subject - supports context-aware pre-filling via query params"""
    
    # Get system configuration to determine active semester type
    config = SystemConfiguration.objects.first()
    if config and config.active_semester_type == 'ODD':
        semester_numbers = [1, 3, 5, 7]
    else:
        semester_numbers = [2, 4, 6, 8]
    
    departments = Department.objects.filter(is_active=True).order_by('code')
    semesters = Semester.objects.filter(number__in=semester_numbers).select_related('department').order_by('department__code', 'number')
    
    # Check for preset department and semester from query params
    preset_dept_id = request.GET.get('dept')
    preset_sem_id = request.GET.get('sem')
    
    # Validate and get preset objects
    preset_dept = None
    preset_sem = None
    
    if preset_dept_id:
        preset_dept = Department.objects.filter(id=preset_dept_id, is_active=True).first()
    if preset_sem_id:
        preset_sem = Semester.objects.select_related('department').filter(id=preset_sem_id).first()
        if preset_sem and not preset_dept:
            preset_dept = preset_sem.department
    
    # Build semester data for JavaScript
    semesters_by_dept = {}
    for sem in semesters:
        dept_id = str(sem.department.id)
        if dept_id not in semesters_by_dept:
            semesters_by_dept[dept_id] = []
        semesters_by_dept[dept_id].append({'id': sem.id, 'number': sem.number})
    
    errors = {}
    form_data = {
        'code': '', 'name': '', 'subject_type': 'THEORY',
        'lecture_hours': '3', 'tutorial_hours': '0', 'practical_hours': '0', 'credits': '3'
    }
    
    if request.method == 'POST':
        code = request.POST.get('code', '').strip().upper()
        short_code = request.POST.get('short_code', '').strip().upper()
        name = request.POST.get('name', '').strip()
        department_id = request.POST.get('department_id', '')
        semester_id = request.POST.get('semester_id', '')
        subject_type = request.POST.get('subject_type', 'THEORY')
        lecture_hours = request.POST.get('lecture_hours', '3')
        tutorial_hours = request.POST.get('tutorial_hours', '0')
        practical_hours = request.POST.get('practical_hours', '0')
        credits = request.POST.get('credits', '3')
        
        form_data = {
            'code': code, 'short_code': short_code, 'name': name, 'department_id': department_id,
            'semester_id': semester_id, 'subject_type': subject_type,
            'lecture_hours': lecture_hours, 'tutorial_hours': tutorial_hours,
            'practical_hours': practical_hours, 'credits': credits
        }
        
        if not code:
            errors['code'] = 'Subject code is required'
        elif Subject.objects.filter(code=code).exists():
            errors['code'] = 'Subject code already exists'
        if not short_code:
            errors['short_code'] = 'Abbreviation is required'
        if not name:
            errors['name'] = 'Subject name is required'
        if not department_id:
            errors['department'] = 'Select a department'
        if not semester_id:
            errors['semester'] = 'Select a semester'
        
        if not errors:
            Subject.objects.create(
                code=code, short_code=short_code, name=name, department_id=department_id,
                semester_id=semester_id, subject_type=subject_type,
                lecture_hours=int(lecture_hours), tutorial_hours=int(tutorial_hours),
                practical_hours=int(practical_hours), credits=int(credits)
            )
            messages.success(request, f'Subject "{code}" added successfully!')
            if preset_dept:
                return redirect(f"{reverse('manage_subjects')}?department={preset_dept.code}")
            return redirect('manage_subjects')
    
    # Prepare department options with selected attribute
    selected_dept_id = form_data.get('department_id', '')
    department_options = []
    for dept in departments:
        department_options.append({
            'id': dept.id,
            'code': dept.code,
            'name': dept.name,
            'selected': 'selected' if str(dept.id) == str(selected_dept_id) else ''
        })
    
    # Prepare type options with checked attribute
    current_type = form_data.get('subject_type', 'THEORY')
    type_options = [
        {'value': 'THEORY', 'label': 'Theory', 'icon': 'bi bi-journal-text text-info me-1', 'checked': 'checked' if current_type == 'THEORY' else ''},
        {'value': 'LAB', 'label': 'Lab', 'icon': 'bi bi-pc-display text-success me-1', 'checked': 'checked' if current_type == 'LAB' else ''},
        {'value': 'ELECTIVE', 'label': 'Elective', 'icon': 'bi bi-bookmark-star text-warning me-1', 'checked': 'checked' if current_type == 'ELECTIVE' else ''},
    ]
    
    return render(request, 'admin/add_subject.html', {
        'page_title': 'Add Subject',
        'submit_label': 'Save Subject',
        'department_options': department_options,
        'semesters_by_dept': json.dumps(semesters_by_dept),
        'type_options': type_options,
        'errors': errors,
        'form_data': form_data,
        'preset_dept': preset_dept,
        'preset_sem': preset_sem,
        'show_dept_dropdown': not preset_dept,
        'show_sem_dropdown': not preset_sem,
        'current_dept_id': str(preset_dept.id) if preset_dept else selected_dept_id,
        'current_sem_id': str(preset_sem.id) if preset_sem else form_data.get('semester_id', ''),
    })


@role_required('ADMIN')
def edit_subject(request, subject_id):
    """Edit an existing subject"""
    
    subject = get_object_or_404(Subject, id=subject_id)
    
    # Get system configuration to determine active semester type
    config = SystemConfiguration.objects.first()
    if config and config.active_semester_type == 'ODD':
        semester_numbers = [1, 3, 5, 7]
    else:
        semester_numbers = [2, 4, 6, 8]
    
    departments = Department.objects.filter(is_active=True).order_by('code')
    semesters = Semester.objects.filter(number__in=semester_numbers).select_related('department').order_by('department__code', 'number')
    
    # Build semester data for JavaScript
    semesters_by_dept = {}
    for sem in semesters:
        dept_id = str(sem.department.id)
        if dept_id not in semesters_by_dept:
            semesters_by_dept[dept_id] = []
        semesters_by_dept[dept_id].append({'id': sem.id, 'number': sem.number})
    
    errors = {}
    
    # Pre-fill form_data with existing subject values
    form_data = {
        'code': subject.code, 'short_code': subject.short_code or '', 'name': subject.name,
        'department_id': str(subject.department_id),
        'semester_id': str(subject.semester_id),
        'subject_type': subject.subject_type,
        'lecture_hours': str(subject.lecture_hours),
        'tutorial_hours': str(subject.tutorial_hours),
        'practical_hours': str(subject.practical_hours),
        'credits': str(subject.credits)
    }
    
    if request.method == 'POST':
        code = request.POST.get('code', '').strip().upper()
        short_code = request.POST.get('short_code', '').strip().upper()
        name = request.POST.get('name', '').strip()
        department_id = request.POST.get('department_id', '')
        semester_id = request.POST.get('semester_id', '')
        subject_type = request.POST.get('subject_type', 'THEORY')
        lecture_hours = request.POST.get('lecture_hours', '3')
        tutorial_hours = request.POST.get('tutorial_hours', '0')
        practical_hours = request.POST.get('practical_hours', '0')
        credits = request.POST.get('credits', '3')
        
        form_data = {
            'code': code, 'short_code': short_code, 'name': name, 'department_id': department_id,
            'semester_id': semester_id, 'subject_type': subject_type,
            'lecture_hours': lecture_hours, 'tutorial_hours': tutorial_hours,
            'practical_hours': practical_hours, 'credits': credits
        }
        
        if not code:
            errors['code'] = 'Subject code is required'
        elif Subject.objects.filter(code=code).exclude(id=subject_id).exists():
            errors['code'] = 'Subject code already exists'
        if not short_code:
            errors['short_code'] = 'Abbreviation is required'
        if not name:
            errors['name'] = 'Subject name is required'
        
        if not errors:
            subject.code = code
            subject.short_code = short_code
            subject.name = name
            subject.department_id = department_id
            subject.semester_id = semester_id
            subject.subject_type = subject_type
            subject.lecture_hours = int(lecture_hours)
            subject.tutorial_hours = int(tutorial_hours)
            subject.practical_hours = int(practical_hours)
            subject.credits = int(credits)
            subject.save()
            messages.success(request, f'Subject "{code}" updated!')
            return redirect('manage_subjects')
    
    # Prepare department options with selected attribute
    department_options = []
    for dept in departments:
        department_options.append({
            'id': dept.id,
            'code': dept.code,
            'name': dept.name,
            'selected': 'selected' if str(dept.id) == form_data['department_id'] else ''
        })
    
    # Prepare type options with checked attribute
    current_type = form_data.get('subject_type', 'THEORY')
    type_options = [
        {'value': 'THEORY', 'label': 'Theory', 'icon': 'bi bi-journal-text text-info me-1', 'checked': 'checked' if current_type == 'THEORY' else ''},
        {'value': 'LAB', 'label': 'Lab', 'icon': 'bi bi-pc-display text-success me-1', 'checked': 'checked' if current_type == 'LAB' else ''},
        {'value': 'ELECTIVE', 'label': 'Elective', 'icon': 'bi bi-bookmark-star text-warning me-1', 'checked': 'checked' if current_type == 'ELECTIVE' else ''},
    ]
    
    return render(request, 'admin/add_subject.html', {
        'page_title': 'Edit Subject',
        'submit_label': 'Update Subject',
        'subject': subject,
        'department_options': department_options,
        'semesters_by_dept': json.dumps(semesters_by_dept),
        'type_options': type_options,
        'errors': errors,
        'form_data': form_data,
        'show_dept_dropdown': True,
        'show_sem_dropdown': True,
        'current_dept_id': form_data['department_id'],
        'current_sem_id': form_data['semester_id'],
    })


@login_required
@require_POST
def toggle_semester_mode(request):
    """Toggle between ODD and EVEN semester mode"""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    config = SystemConfiguration.objects.first()
    if not config:
        config = SystemConfiguration.objects.create()
    
    mode = request.POST.get('mode')
    if mode in ['ODD', 'EVEN']:
        config.active_semester_type = mode
        config.save()
        return JsonResponse({'success': True, 'mode': mode})
    
    return JsonResponse({'error': 'Invalid mode'}, status=400)


@login_required
@require_POST
def generate_timetable_view(request):
    """Generate timetable for an entire department using GA"""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    department_id = request.POST.get('department_id')
    
    if not department_id:
        return JsonResponse({'error': 'Department ID required'}, status=400)
    
    config = SystemConfiguration.objects.first()
    semester_instance = config.get_semester_instance() if config else '2024-ODD'
    
    try:
        from .genetic_algorithm import generate_department_timetable
        result = generate_department_timetable(int(department_id), semester_instance)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)



@login_required
def initialize_time_slots(request):
    """Initialize fixed time slots with validation"""
    if not request.user.is_staff:
        return redirect('home')
    
    # Check if slots already exist
    slots_count = TimeSlot.objects.count()
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'initialize':
            if slots_count > 0:
                messages.error(request, 'Time slots already exist. Use "Re-initialize" to replace them.')
                return redirect('init_time_slots')
            
            _create_time_slots()
            messages.success(request, f'Time slots initialized successfully! Created {TimeSlot.objects.count()} slots (35 teaching + 15 non-teaching).')
            return redirect('admin_dashboard')
        
        elif action == 'reinitialize':
            # Check if any timetable entries exist
            if TimetableEntry.objects.exists():
                messages.error(request, 'Cannot re-initialize: Active timetables exist. Delete them first from Django Admin.')
                return redirect('init_time_slots')
            
            # Delete existing slots
            TimeSlot.objects.all().delete()
            _create_time_slots()
            messages.success(request, f'Time slots re-initialized successfully! Created {TimeSlot.objects.count()} slots (35 teaching + 15 non-teaching).')
            return redirect('admin_dashboard')
    
    # PRE-PROCESS ALL DATA IN BACKEND - NO LOGIC IN TEMPLATE
    display_slots = []
    
    if slots_count > 0:
        # Get first day's slots to show structure (all days have same structure)
        # Order by start_time to display in chronological order
        first_day_slots = TimeSlot.objects.filter(day='MON').order_by('start_time')
        
        for slot in first_day_slots:
            # Pre-calculate all display properties
            # Determine period display label
            if slot.slot_type == 'LUNCH':
                period_label = 'Lunch Break'
            elif slot.slot_type == 'RECESS':
                period_label = 'Recess Break'
            else:
                period_label = str(slot.period)
            
            slot_data = {
                'period_display': period_label,
                'start_time': slot.start_time,
                'end_time': slot.end_time,
                'duration_minutes': slot.duration_minutes,
                'is_lunch': slot.slot_type == 'LUNCH',
                'type_badge_class': _get_badge_class(slot.slot_type),
                'type_display': _get_type_display(slot.slot_type),
            }
            display_slots.append(slot_data)
    
    # Teaching and lunch slot counts
    teaching_count = TimeSlot.objects.filter(slot_type__in=['MORNING', 'AFTERNOON']).count()
    lunch_count = TimeSlot.objects.filter(slot_type='LUNCH').count()
    
    return render(request, 'admin/init_slots.html', {
        'slots_exist': slots_count > 0,
        'slots_count': slots_count,
        'teaching_count': teaching_count,
        'lunch_count': lunch_count,
        'display_slots': display_slots,  # Pre-processed, ready to display
        'has_timetables': TimetableEntry.objects.exists()
    })


def _get_badge_class(slot_type):
    """Return Bootstrap badge class for slot type"""
    if slot_type == 'MORNING':
        return 'bg-info'
    elif slot_type == 'AFTERNOON':
        return 'bg-warning text-dark'
    elif slot_type == 'LUNCH':
        return 'bg-secondary'
    elif slot_type == 'RECESS':
        return 'bg-primary'
    return 'bg-primary'


def _get_type_display(slot_type):
    """Return human-readable display name for slot type"""
    if slot_type == 'MORNING':
        return 'Morning'
    elif slot_type == 'AFTERNOON':
        return 'Afternoon'
    elif slot_type == 'LUNCH':
        return 'Non-Teaching'
    elif slot_type == 'RECESS':
        return 'Recess'
    return slot_type


def _create_time_slots():
    """Internal function to create standard time slot configuration"""
    from datetime import time
    
    days = ['MON', 'TUE', 'WED', 'THU', 'FRI']
    
    # Define slot structure: (period, start, end, type)
    # Use negative period numbers for breaks to avoid conflicts with teaching periods
    # Teaching periods: 1-7
    slot_structure = [
        (1, time(8, 45), time(9, 30), 'MORNING'),      # Period 1: 45 min
        (2, time(9, 30), time(10, 25), 'MORNING'),     # Period 2: 55 min
        (-1, time(10, 25), time(10, 35), 'RECESS'),    # Recess 1: 10 min
        (3, time(10, 35), time(11, 30), 'MORNING'),    # Period 3: 55 min
        (4, time(11, 30), time(12, 20), 'MORNING'),    # Period 4: 50 min
        (0, time(12, 20), time(13, 5), 'LUNCH'),       # Lunch: 45 min (period 0)
        (5, time(13, 5), time(13, 55), 'AFTERNOON'),   # Period 5: 50 min
        (-2, time(13, 55), time(14, 5), 'RECESS'),     # Recess 2: 10 min
        (6, time(14, 5), time(14, 55), 'AFTERNOON'),   # Period 6: 50 min
        (7, time(14, 55), time(15, 45), 'AFTERNOON'),  # Period 7: 50 min
    ]
    
    for day in days:
        for period, start, end, slot_type in slot_structure:
            TimeSlot.objects.create(
                day=day,
                period=period,
                start_time=start,
                end_time=end,
                slot_type=slot_type,
                is_locked=True
            )


# ============ TEACHER DASHBOARD ============

@role_required('TEACHER')
def teacher_dashboard(request):
    """Teacher dashboard - view timetable and manage preferences"""
    try:
        faculty = Faculty.objects.get(user=request.user)
    except Faculty.DoesNotExist:
        messages.error(request, 'No faculty profile linked to this account.')
        return redirect('home')
    
    config = SystemConfiguration.objects.first()
    semester_instance = config.get_semester_instance() if config else '2024-ODD'
    
    # Get faculty's timetable entries
    entries = TimetableEntry.objects.filter(
        faculty=faculty,
        semester_instance=semester_instance
    ).select_related('class_section', 'subject', 'time_slot').order_by('time_slot')
    
    # Also get entries where faculty is assistant
    assistant_entries = TimetableEntry.objects.filter(
        assistant_faculty=faculty,
        semester_instance=semester_instance
    ).select_related('class_section', 'subject', 'time_slot')
    
    # Build lookup dictionary for fast access
    days = ['MON', 'TUE', 'WED', 'THU', 'FRI']
    periods = list(range(1, 8))
    
    # Create lookup dictionary: (day, period) -> entry data
    slot_lookup = {}
    
    for entry in entries:
        key = (entry.time_slot.day, entry.time_slot.period)
        slot_lookup[key] = {
            'subject': entry.subject.code,
            'class': str(entry.class_section),
            'type': 'main',
            'is_lab': entry.is_lab_session
        }
    
    for entry in assistant_entries:
        key = (entry.time_slot.day, entry.time_slot.period)
        if key not in slot_lookup:  # Only add if not already filled
            slot_lookup[key] = {
                'subject': entry.subject.code,
                'class': str(entry.class_section),
                'type': 'assistant',
                'is_lab': entry.is_lab_session
            }
    
    # Build complete grid as list of rows for easy template iteration
    timetable_rows = []
    for period in periods:
        row = {
            'period': period,
            'cells': []
        }
        for day in days:
            cell_data = slot_lookup.get((day, period))
            if cell_data:
                row['cells'].append({
                    'has_class': True,
                    'subject': cell_data['subject'],
                    'class_name': cell_data['class'],
                    'is_lab': cell_data['is_lab'],
                    'is_assistant': cell_data['type'] == 'assistant'
                })
            else:
                row['cells'].append({
                    'has_class': False
                })
        timetable_rows.append(row)
    
    context = {
        'faculty': faculty,
        'timetable_rows': timetable_rows,
        'days': days,
        'config': config,
        'has_timetable': bool(slot_lookup),
    }
    return render(request, 'faculty/dashboard.html', context)



@role_required('TEACHER')
@require_POST
def update_preferences(request):
    """Update faculty preferences"""
    try:
        faculty = Faculty.objects.get(user=request.user)
    except Faculty.DoesNotExist:
        return JsonResponse({'error': 'Faculty not found'}, status=404)
    
    preferences = request.POST.get('preferences', '')
    faculty.preferences = preferences
    faculty.save()
    
    return JsonResponse({'success': True})


# ============ STUDENT DASHBOARD ============

@role_required('STUDENT')
def student_dashboard(request):
    """Student dashboard - view timetable and manage class assignment"""
    config = SystemConfiguration.objects.first()
    
    # Get or create student profile
    student_profile, created = StudentProfile.objects.get_or_create(user=request.user)
    
    # Initialize timetable and subjects data
    timetable_data = None
    subjects_count = 0
    class_display = None
    
    # If student has class assigned, fetch timetable
    if student_profile.class_section:
        class_section = student_profile.class_section
        semester = class_section.semester
        department = semester.department
        
        # Build class display name like 'S5-CS1'
        class_display = f"S{semester.number}-{department.code}{class_section.name}"
        
        # Fetch timetable entries for this class
        entries = TimetableEntry.objects.filter(
            class_section=class_section,
            semester_instance=config.get_semester_instance() if config else None
        ).select_related('subject', 'faculty', 'time_slot', 'assistant_faculty')
        
        if entries.exists():
            timetable_data = _build_timetable_grid(entries, 'class')
            subjects_count = entries.values('subject').distinct().count()
    
    # Fetch available departments for selection form
    departments = Department.objects.filter(is_active=True).order_by('code')
    
    context = {
        'config': config,
        'user': request.user,
        'student_profile': student_profile,
        'departments': departments,
        'timetable_data': timetable_data,
        'subjects_count': subjects_count,
        'class_display': class_display,
    }
    return render(request, 'student/dashboard.html', context)


@login_required
def get_class_sections(request, department_id, semester_num):
    """API: Get class sections for a department and semester number"""
    try:
        # Find the semester for this department and number
        semester = Semester.objects.filter(
            department_id=department_id,
            number=semester_num
        ).first()
        
        if not semester:
            return JsonResponse({'success': False, 'error': 'Semester not found'}, status=404)
        
        # Get all class sections for this semester
        sections = ClassSection.objects.filter(semester=semester).order_by('name')
        
        department = Department.objects.get(id=department_id)
        
        sections_data = []
        for section in sections:
            # Build display name like 'S5-CS1'
            display_name = f"S{semester_num}-{department.code}{section.name}"
            sections_data.append({
                'id': section.id,
                'name': section.name,
                'display_name': display_name,
            })
        
        return JsonResponse({
            'success': True,
            'sections': sections_data,
            'semester_id': semester.id,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def update_student_class(request):
    """API: Save student's class selection"""
    try:
        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        class_section_id = data.get('class_section_id')
        
        if not class_section_id:
            return JsonResponse({'success': False, 'error': 'Class section is required'}, status=400)
        
        # Validate class section exists
        class_section = ClassSection.objects.select_related('semester__department').filter(id=class_section_id).first()
        if not class_section:
            return JsonResponse({'success': False, 'error': 'Invalid class section'}, status=404)
        
        # Get or create student profile and update
        student_profile, created = StudentProfile.objects.get_or_create(user=request.user)
        student_profile.class_section = class_section
        student_profile.save()
        
        # Build display name
        semester = class_section.semester
        department = semester.department
        class_display = f"S{semester.number}-{department.code}{class_section.name}"
        
        return JsonResponse({
            'success': True,
            'message': f'Successfully assigned to {class_display}',
            'class_display': class_display,
            'semester_type': semester.semester_type,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def get_semesters_for_department(request, department_id):
    """API: Get available semesters for a department based on active semester type"""
    try:
        config = SystemConfiguration.objects.first()
        
        # Determine which semester numbers to show based on ODD/EVEN mode
        if config and config.active_semester_type == 'ODD':
            valid_numbers = [1, 3, 5, 7]
        else:
            valid_numbers = [2, 4, 6, 8]
        
        semesters = Semester.objects.filter(
            department_id=department_id,
            number__in=valid_numbers
        ).order_by('number')
        
        semesters_data = [{'number': s.number, 'id': s.id} for s in semesters]
        
        return JsonResponse({
            'success': True,
            'semesters': semesters_data,
            'active_type': config.active_semester_type if config else 'ODD'
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============ TIMETABLE VIEWS ============


def timetable_view(request):
    """View timetables - department-wise or faculty-wise (ALL LOGIC IN BACKEND)"""
    view_mode = request.GET.get('mode', 'department')  # 'department' or 'faculty'
    selected_id = request.GET.get('id')
    
    config = SystemConfiguration.objects.first()
    
    # Prepare data based on mode - ALL FILTERING/GROUPING IN BACKEND
    if view_mode == 'department':
        context = _prepare_department_view(selected_id, config)
    else:
        context = _prepare_faculty_view(selected_id, config)
    
    context['view_mode'] = view_mode
    context['config'] = config
    
    return render(request, 'timetable/view.html', context)


def _prepare_department_view(department_id, config):
    """
    Prepare department-wise timetable data.
    Returns pre-grouped, pre-processed data ready for template display.
    NO LOGIC IN TEMPLATE - ALL DONE HERE.
    """
    semester_instance = config.get_semester_instance() if config else '2024-ODD'
    
    # Get all departments for selection
    departments = Department.objects.filter(is_active=True).order_by('code')
    departments_list = []
    for dept in departments:
        departments_list.append({
            'id': dept.id,
            'name': dept.name,
            'code': dept.code,
            'full_name': f'{dept.code} - {dept.name}',
            'is_selected': str(dept.id) == str(department_id) if department_id else False
        })
    
    result = {
        'departments': departments_list,
        'selected_department': None,
        'timetable_data': [],
        'has_data': False
    }
    
    if not department_id:
        return result
    
    # Get selected department
    try:
        department = Department.objects.get(id=department_id)
        result['selected_department'] = {
            'id': department.id,
            'name': department.name,
            'code': department.code,
            'full_name': f'{department.code} - {department.name}'
        }
    except Department.DoesNotExist:
        return result
    
    # Determine semester numbers based on active type
    semester_numbers = [1, 3, 5, 7] if config and config.active_semester_type == 'ODD' else [2, 4, 6, 8]
    
    # Get semesters for this department
    semesters = Semester.objects.filter(
        department_id=department_id,
        number__in=semester_numbers
    ).order_by('number')
    
    # Build timetable data structure: Department → Semesters → Classes → Grids
    timetable_data = []
    
    for semester in semesters:
        semester_data = {
            'semester_number': semester.number,
            'semester_name': f'Semester {semester.number}',
            'semester_display': str(semester),
            'classes': []
        }
        
        classes = ClassSection.objects.filter(semester=semester).order_by('name')
        
        for class_section in classes:
            entries = TimetableEntry.objects.filter(
                class_section=class_section,
                semester_instance=semester_instance
            ).select_related('subject', 'faculty', 'time_slot', 'assistant_faculty')
            
            if not entries.exists():
                continue  # Skip classes with no timetable generated yet
            
            # Build timetable grid with pre-processed data
            grid_result = _build_timetable_grid(entries, 'class')
            
            class_data = {
                'class_id': class_section.id,
                'class_name': class_section.name,
                'class_display': f'{semester}-{class_section.name}',
                'full_name': f'{semester} - Section {class_section.name}',
                'timetable_grid': grid_result['grid'],
                'period_headers': grid_result['period_headers'],
                'legend': grid_result['legend'],
                'entry_count': entries.count()
            }
            semester_data['classes'].append(class_data)
        
        if semester_data['classes']:  # Only add semester if it has classes with timetables
            timetable_data.append(semester_data)
    
    result['timetable_data'] = timetable_data
    result['has_data'] = len(timetable_data) > 0
    
    return result


def _prepare_faculty_view(faculty_id, config):
    """
    Prepare faculty-wise timetable data.
    Shows consolidated schedule for a faculty member.
    """
    semester_instance = config.get_semester_instance() if config else '2024-ODD'
    
    # Get all active faculty for selection, grouped by department
    departments = Department.objects.filter(is_active=True).order_by('code')
    grouped_faculties = []
    
    for dept in departments:
        dept_faculties = Faculty.objects.filter(department=dept, is_active=True).order_by('name')
        if not dept_faculties.exists():
            continue
            
        fac_list = []
        for fac in dept_faculties:
            fac_list.append({
                'id': fac.id,
                'name': fac.name,
                'designation': fac.get_designation_display(),
                'full_display': f'{fac.name} ({fac.get_designation_display()})',
                'is_selected': str(fac.id) == str(faculty_id) if faculty_id else False
            })
            
        grouped_faculties.append({
            'department_id': dept.id,
            'department_name': dept.name,
            'department_code': dept.code,
            'faculties': fac_list
        })
        
    # Also handle faculty without departments (if any)
    unassigned_faculties = Faculty.objects.filter(department__isnull=True, is_active=True).order_by('name')
    if unassigned_faculties.exists():
        fac_list = []
        for fac in unassigned_faculties:
            fac_list.append({
                'id': fac.id,
                'name': fac.name,
                'designation': fac.get_designation_display(),
                'full_display': f'{fac.name} ({fac.get_designation_display()})',
                'is_selected': str(fac.id) == str(faculty_id) if faculty_id else False
            })
        grouped_faculties.append({
            'department_id': 'unassigned',
            'department_name': 'Unassigned',
            'department_code': 'None',
            'faculties': fac_list
        })
    
    result = {
        'grouped_faculties': grouped_faculties,
        'selected_faculty': None,
        'timetable_grid': [],
        'has_data': False
    }
    
    if not faculty_id:
        return result
    
    try:
        faculty = Faculty.objects.get(id=faculty_id)
        result['selected_faculty'] = {
            'id': faculty.id,
            'name': faculty.name,
            'designation': faculty.get_designation_display(),
            'department': faculty.department.code if faculty.department else 'N/A'
        }
    except Faculty.DoesNotExist:
        return result
    
    # Get all entries where this faculty is assigned (main or assistant)
    entries = TimetableEntry.objects.filter(
        Q(faculty_id=faculty_id) | Q(assistant_faculty_id=faculty_id),
        semester_instance=semester_instance
    ).select_related('class_section', 'subject', 'time_slot', 'faculty', 'assistant_faculty')
    
    if entries.exists():
        grid_result = _build_timetable_grid(entries, 'faculty', faculty_id)
        result['timetable_grid'] = grid_result['grid']
        result['period_headers'] = grid_result['period_headers']
        result['legend'] = grid_result['legend']
        result['has_data'] = True
    
    return result


def _get_faculty_initials(name):
    """Generate initials from faculty name (e.g. Meera V M -> MVM)"""
    if not name:
        return ""
    # Filter out titles like Dr., Mr., Ms., Mrs.
    cleaned = name
    for title in ["Dr.", "Mr.", "Ms.", "Mrs.", "Prof."]:
        cleaned = cleaned.replace(title, "")
    
    parts = cleaned.strip().split()
    initials = "".join([p[0].upper() for p in parts if p])
    return initials

def _build_timetable_grid(entries, view_type, faculty_id=None):
    """
    Build timetable grid structure with pre-processed, ready-to-display data.
    Returns dict with grid AND legend data.
    Transposed layout: Days as rows, Periods as columns.
    """
    days = ['MON', 'TUE', 'WED', 'THU', 'FRI']
    day_names = {
        'MON': 'Monday',
        'TUE': 'Tuesday', 
        'WED': 'Wednesday',
        'THU': 'Thursday',
        'FRI': 'Friday'
    }
    periods = range(1, 8)
    
    # Get period times from database
    period_times = _get_period_times()
    
    # Build period headers for columns
    period_headers = []
    for period in periods:
        period_headers.append({
            'number': period,
            'display': f'P{period}',
            'time': period_times.get(period, '')
        })
    
    # Build grid data - now with DAYS as rows and PERIODS as columns
    grid_data = []
    
    # Track subjects/faculty for legend
    legend_map = {} # code -> {name, abbreviation, faculty_list}
    
    for day in days:
        day_row = {
            'day_code': day,
            'day_name': day_names[day],
            'periods': []
        }
        
        for period in periods:
            cell = {
                'period_number': period,
                'has_entry': False,
                'display_line1': '', # Subject name
                'display_line2': '', # Faculty name
                'display_line3': '', # Assistant name
                'tooltip': '',
                'css_class': 'empty-cell'
            }
            
            matching_entry = None
            for entry in entries:
                if entry.time_slot.day == day and entry.time_slot.period == period:
                    matching_entry = entry
                    break
            
            if matching_entry:
                subj = matching_entry.subject
                abbr = subj.short_code or subj.code
                fac_name = matching_entry.faculty.name
                
                # Update legend map (keeping it for reference)
                if subj.code not in legend_map:
                    legend_map[subj.code] = {
                        'code': subj.code,
                        'name': subj.name,
                        'abbr': abbr,
                        'faculty_data': []
                    }
                
                if fac_name not in [f['name'] for f in legend_map[subj.code]['faculty_data']]:
                    legend_map[subj.code]['faculty_data'].append({'name': fac_name, 'initials': _get_faculty_initials(fac_name)})
                
                if matching_entry.assistant_faculty:
                    asst_name = matching_entry.assistant_faculty.name
                    if asst_name not in [f['name'] for f in legend_map[subj.code]['faculty_data']]:
                        legend_map[subj.code]['faculty_data'].append({'name': asst_name, 'initials': _get_faculty_initials(asst_name)})

                if view_type == 'class':
                    cell.update({
                        'has_entry': True,
                        'display_line1': subj.name,
                        'display_line2': fac_name,
                        'display_line3': f"& {matching_entry.assistant_faculty.name}" if matching_entry.assistant_faculty else "",
                        'tooltip': f"{subj.code}: {subj.name}",
                        'css_class': 'lab-cell' if matching_entry.is_lab_session else 'theory-cell'
                    })
                else:
                    is_assistant = matching_entry.assistant_faculty_id and str(matching_entry.assistant_faculty_id) == str(faculty_id)
                    cell.update({
                        'has_entry': True,
                        'display_line1': subj.name,
                        'display_line2': str(matching_entry.class_section),
                        'display_line3': '(Asst)' if is_assistant else '',
                        'tooltip': f"{subj.code}: {subj.name}",
                        'css_class': 'lab-cell' if matching_entry.is_lab_session else 'theory-cell'
                    })
            
            day_row['periods'].append(cell)
        
        grid_data.append(day_row)
    
    # Format legend for template
    legend_list = []
    for code, data in sorted(legend_map.items()):
        # Join multiple faculty with comma or slash
        fac_init_str = "/".join([f['initials'] for f in data['faculty_data']])
        fac_name_str = ", ".join([f['name'] for f in data['faculty_data']])
        
        legend_list.append({
            'code': code,
            'name': data['name'],
            'abbr': data['abbr'],
            'fac_initials': fac_init_str,
            'fac_names': fac_name_str
        })
    
    return {
        'grid': grid_data,
        'period_headers': period_headers,
        'legend': legend_list
    }


def _get_period_times():
    """
    Get period times from database (no hardcoding in template).
    Returns dict mapping period number to start time string.
    """
    slots = TimeSlot.objects.filter(
        slot_type__in=['MORNING', 'AFTERNOON']
    ).order_by('period').values('period', 'start_time')
    
    return {slot['period']: slot['start_time'].strftime('%H:%M') for slot in slots}


def export_timetable_pdf(request):
    """Export timetable as PDF"""
    from io import BytesIO
    from xhtml2pdf import pisa
    from django.template.loader import get_template
    
    view_type = request.GET.get('type', 'class')
    selected_id = request.GET.get('id')
    
    if not selected_id:
        return HttpResponse('No selection made', status=400)
    
    config = SystemConfiguration.objects.first()
    semester_instance = config.get_semester_instance() if config else '2024-ODD'
    
    days = ['MON', 'TUE', 'WED', 'THU', 'FRI']
    periods = range(1, 8)
    
    if view_type == 'class':
        class_section = ClassSection.objects.get(id=selected_id)
        entries = TimetableEntry.objects.filter(
            class_section_id=selected_id,
            semester_instance=semester_instance
        ).select_related('subject', 'faculty', 'time_slot')
        
        timetable_grid = {day: {p: None for p in periods} for day in days}
        for entry in entries:
            day = entry.time_slot.day
            period = entry.time_slot.period
            timetable_grid[day][period] = {
                'subject': entry.subject.code,
                'faculty': entry.faculty.name[:10],
                'is_lab': entry.is_lab_session
            }
        
        title = f"Timetable - {class_section}"
    else:
        faculty = Faculty.objects.get(id=selected_id)
        entries = TimetableEntry.objects.filter(
            Q(faculty_id=selected_id) | Q(assistant_faculty_id=selected_id),
            semester_instance=semester_instance
        ).select_related('class_section', 'subject', 'time_slot')
        
        timetable_grid = {day: {p: None for p in periods} for day in days}
        for entry in entries:
            day = entry.time_slot.day
            period = entry.time_slot.period
            timetable_grid[day][period] = {
                'subject': entry.subject.code,
                'class': str(entry.class_section),
                'is_lab': entry.is_lab_session
            }
        
        title = f"Timetable - {faculty.name}"
    
    template = get_template('timetable/pdf_template.html')
    html = template.render({
        'title': title,
        'timetable_grid': timetable_grid,
        'days': days,
        'periods': periods,
        'config': config
    })
    
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html.encode('utf-8')), result)
    
    if pdf.err:
        return HttpResponse('Error generating PDF', status=500)
    
    response = HttpResponse(result.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{title.replace(" ", "_")}.pdf"'
    return response


# ============ TIMETABLE REST API ENDPOINTS ============

def api_timetable_departments(request):
    """API: Get all active departments"""
    departments = Department.objects.filter(is_active=True).order_by('code')
    data = [{'id': d.id, 'code': d.code, 'name': d.name, 'full_name': f'{d.code} - {d.name}'} for d in departments]
    return JsonResponse({'success': True, 'departments': data})


def api_timetable_semesters(request):
    """API: Get available semesters based on active semester type"""
    config = SystemConfiguration.objects.first()
    active_type = config.active_semester_type if config else 'ODD'
    
    # ODD = 1,3,5,7; EVEN = 2,4,6,8
    semester_numbers = [1, 3, 5, 7] if active_type == 'ODD' else [2, 4, 6, 8]
    
    data = [{'number': n, 'display': f'Semester {n}'} for n in semester_numbers]
    return JsonResponse({'success': True, 'semesters': data, 'active_type': active_type})


def api_timetable_sections(request):
    """API: Get sections for a department and semester"""
    department_id = request.GET.get('department')
    semester_num = request.GET.get('semester')
    
    if not department_id or not semester_num:
        return JsonResponse({'success': False, 'error': 'Missing department or semester parameter'})
    
    try:
        department = Department.objects.get(id=department_id)
        semester = Semester.objects.filter(department_id=department_id, number=semester_num).first()
        
        if not semester:
            return JsonResponse({'success': True, 'sections': []})
        
        sections = ClassSection.objects.filter(semester=semester).order_by('name')
        data = [{
            'id': s.id,
            'name': s.name,
            'display': f'S{semester_num}-{department.code}{s.name}'
        } for s in sections]
        
        return JsonResponse({'success': True, 'sections': data, 'semester_id': semester.id})
    except Department.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Department not found'})


def api_timetable_grid(request):
    """API: Get timetable grid data for a section"""
    section_id = request.GET.get('section')
    
    if not section_id:
        return JsonResponse({'success': False, 'error': 'Missing section parameter'})
    
    try:
        class_section = ClassSection.objects.select_related('semester__department').get(id=section_id)
        config = SystemConfiguration.objects.first()
        semester_instance = config.get_semester_instance() if config else '2024-ODD'
        
        entries = TimetableEntry.objects.filter(
            class_section=class_section,
            semester_instance=semester_instance
        ).select_related('subject', 'faculty', 'time_slot', 'assistant_faculty')
        
        if not entries.exists():
            return JsonResponse({'success': True, 'has_data': False, 'message': 'No timetable generated for this section'})
        
        # Build the grid data
        grid_result = _build_timetable_grid(entries, 'class')
        
        semester = class_section.semester
        department = semester.department
        
        return JsonResponse({
            'success': True,
            'has_data': True,
            'section_display': f'S{semester.number}-{department.code}{class_section.name}',
            'section_full_name': f'{semester} - Section {class_section.name}',
            'period_headers': grid_result['period_headers'],
            'grid': grid_result['grid'],
            'legend': grid_result['legend'],
            'entry_count': entries.count()
        })
    except ClassSection.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Section not found'})


def api_timetable_faculty_list(request):
    """API: Get all faculty grouped by department"""
    departments = Department.objects.filter(is_active=True).order_by('code')
    grouped_faculties = []
    
    for dept in departments:
        faculties = Faculty.objects.filter(department=dept, is_active=True).order_by('name')
        if not faculties.exists():
            continue
        
        fac_list = [{
            'id': f.id,
            'name': f.name,
            'designation': f.get_designation_display()
        } for f in faculties]
        
        grouped_faculties.append({
            'department_code': dept.code,
            'department_name': dept.name,
            'faculties': fac_list
        })
    
    return JsonResponse({'success': True, 'grouped_faculties': grouped_faculties})


def api_timetable_faculty_grid(request):
    """API: Get timetable grid for a specific faculty"""
    faculty_id = request.GET.get('faculty')
    
    if not faculty_id:
        return JsonResponse({'success': False, 'error': 'Missing faculty parameter'})
    
    try:
        faculty = Faculty.objects.select_related('department').get(id=faculty_id)
        config = SystemConfiguration.objects.first()
        semester_instance = config.get_semester_instance() if config else '2024-ODD'
        
        entries = TimetableEntry.objects.filter(
            Q(faculty_id=faculty_id) | Q(assistant_faculty_id=faculty_id),
            semester_instance=semester_instance
        ).select_related('class_section', 'subject', 'time_slot', 'faculty', 'assistant_faculty')
        
        if not entries.exists():
            return JsonResponse({'success': True, 'has_data': False, 'message': 'No schedule for this faculty'})
        
        grid_result = _build_timetable_grid(entries, 'faculty', faculty_id)
        
        return JsonResponse({
            'success': True,
            'has_data': True,
            'faculty_name': faculty.name,
            'faculty_designation': faculty.get_designation_display(),
            'faculty_department': faculty.department.code if faculty.department else 'N/A',
            'period_headers': grid_result['period_headers'],
            'grid': grid_result['grid'],
            'legend': grid_result['legend']
        })
    except Faculty.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Faculty not found'})
