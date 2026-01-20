from django.shortcuts import render, redirect, get_object_or_404
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
    FacultySubjectAssignment, TimeSlot, TimetableEntry, SystemConfiguration
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
    """Login page for faculty"""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            # Check if admin or faculty
            if user.is_staff:
                return redirect('admin_dashboard')
            return redirect('faculty_dashboard')
        else:
            messages.error(request, 'Invalid username or password')
    
    return render(request, 'login.html')


def logout_view(request):
    """Logout user"""
    logout(request)
    return redirect('home')


# ============ ADMIN DASHBOARD ============

@login_required
def admin_dashboard(request):
    """Main admin dashboard"""
    if not request.user.is_staff:
        messages.error(request, 'Access denied. Admin privileges required.')
        return redirect('home')
    
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


@login_required
def manage_departments(request):
    """Manage departments"""
    if not request.user.is_staff:
        return redirect('home')
    
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


@login_required
def add_department(request):
    """Add or edit a department - dedicated page"""
    if not request.user.is_staff:
        return redirect('home')
    
    errors = {}
    form_data = {}
    department = None
    
    # Check if editing an existing department
    edit_id = request.GET.get('edit')
    if edit_id:
        department = Department.objects.filter(id=edit_id).first()
    
    if request.method == 'POST':
        action = request.POST.get('action')
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        description = request.POST.get('description', '').strip()
        is_active = request.POST.get('is_active', '1') == '1'
        
        form_data = {
            'name': name,
            'code': code,
            'description': description,
            'is_active': '1' if is_active else '0'
        }
        
        # Validation
        if not name:
            errors['name'] = 'Department name is required'
        
        if not code:
            errors['code'] = 'Department code is required'
        else:
            # Check for duplicate code (excluding current department if editing)
            existing = Department.objects.filter(code=code)
            if action == 'update':
                dept_id = request.POST.get('department_id')
                existing = existing.exclude(id=dept_id)
            if existing.exists():
                errors['code'] = 'A department with this code already exists'
        
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
        'department': department
    })


@login_required
def manage_semesters(request):
    """Manage semesters and classes"""
    if not request.user.is_staff:
        return redirect('home')
    
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


@login_required
def add_semester(request):
    """Add a new semester - dedicated page"""
    if not request.user.is_staff:
        return redirect('home')
    
    errors = {}
    form_data = {}
    
    if request.method == 'POST':
        dept_id = request.POST.get('department_id', '').strip()
        number = request.POST.get('number', '').strip()
        academic_year = request.POST.get('academic_year', '').strip()
        
        form_data = {
            'department_id': dept_id,
            'number': number,
            'academic_year': academic_year
        }
        
        # Validation
        if not dept_id:
            errors['department'] = 'Please select a department'
        
        if not number:
            errors['number'] = 'Please select a semester'
        
        if not academic_year:
            errors['academic_year'] = 'Academic year is required'
        
        if dept_id and number and not errors:
            # Check if semester already exists
            existing = Semester.objects.filter(department_id=dept_id, number=number).exists()
            if existing:
                errors['number'] = f'Semester {number} already exists for this department'
            else:
                Semester.objects.create(department_id=dept_id, number=number)
                messages.success(request, f'Semester S{number} created successfully.')
                return redirect('manage_semesters')
    
    departments = Department.objects.filter(is_active=True)
    selected_dept = request.GET.get('department')
    
    return render(request, 'admin/add_semester.html', {
        'departments': departments,
        'selected_dept': int(selected_dept) if selected_dept else None,
        'errors': errors,
        'form_data': form_data
    })


@login_required
def add_class(request):
    """Add a new class section - dedicated page"""
    if not request.user.is_staff:
        return redirect('home')
    
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


@login_required
def manage_faculty(request):
    """Manage faculty members"""
    if not request.user.is_staff:
        return redirect('home')
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add':
            Faculty.objects.create(
                name=request.POST.get('name'),
                email=request.POST.get('email'),
                designation=request.POST.get('designation'),
                preferences=request.POST.get('preferences', '')
            )
            messages.success(request, 'Faculty added successfully.')
        
        elif action == 'update':
            faculty_id = request.POST.get('faculty_id')
            faculty = Faculty.objects.get(id=faculty_id)
            faculty.name = request.POST.get('name')
            faculty.email = request.POST.get('email')
            faculty.designation = request.POST.get('designation')
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
    
    faculties = Faculty.objects.all().order_by('designation', 'name')
    
    return render(request, 'admin/faculty.html', {'faculties': faculties})


@login_required
def manage_subjects(request):
    """Manage subjects"""
    if not request.user.is_staff:
        return redirect('home')
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add':
            Subject.objects.create(
                name=request.POST.get('name'),
                code=request.POST.get('code'),
                department_id=request.POST.get('department_id'),
                semester_id=request.POST.get('semester_id'),
                subject_type=request.POST.get('subject_type'),
                hours_per_week=request.POST.get('hours_per_week', 3),
                credits=request.POST.get('credits', 3)
            )
            messages.success(request, 'Subject added successfully.')
        
        elif action == 'delete':
            subject_id = request.POST.get('subject_id')
            Subject.objects.filter(id=subject_id).delete()
            messages.success(request, 'Subject deleted.')
    
    subjects = Subject.objects.select_related('department', 'semester')
    departments = Department.objects.all()
    semesters = Semester.objects.all()
    
    return render(request, 'admin/subjects.html', {
        'subjects': subjects,
        'departments': departments,
        'semesters': semesters
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
    """Generate timetable for a semester using GA"""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    semester_id = request.POST.get('semester_id')
    
    if not semester_id:
        return JsonResponse({'error': 'Semester ID required'}, status=400)
    
    config = SystemConfiguration.objects.first()
    semester_instance = config.get_semester_instance() if config else '2024-ODD'
    
    try:
        result = generate_timetable(int(semester_id), semester_instance)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def initialize_time_slots(request):
    """Initialize default time slots (7 periods x 5 days)"""
    if not request.user.is_staff:
        return redirect('home')
    
    if request.method == 'POST':
        # Clear existing slots
        TimeSlot.objects.all().delete()
        
        days = ['MON', 'TUE', 'WED', 'THU', 'FRI']
        times = [
            ('09:00', '09:50'),
            ('09:50', '10:40'),
            ('10:50', '11:40'),
            ('11:40', '12:30'),
            ('13:30', '14:20'),
            ('14:20', '15:10'),
            ('15:20', '16:10'),
        ]
        
        from datetime import datetime
        
        for day in days:
            for period, (start, end) in enumerate(times, 1):
                TimeSlot.objects.create(
                    day=day,
                    period=period,
                    start_time=datetime.strptime(start, '%H:%M').time(),
                    end_time=datetime.strptime(end, '%H:%M').time()
                )
        
        messages.success(request, 'Time slots initialized successfully (7 periods × 5 days).')
        return redirect('admin_dashboard')
    
    slots_exist = TimeSlot.objects.exists()
    return render(request, 'admin/init_slots.html', {'slots_exist': slots_exist})


# ============ FACULTY DASHBOARD ============

@login_required
def faculty_dashboard(request):
    """Faculty dashboard - view timetable and manage preferences"""
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
    
    # Build timetable grid
    days = ['MON', 'TUE', 'WED', 'THU', 'FRI']
    periods = range(1, 8)
    
    timetable_grid = {day: {p: None for p in periods} for day in days}
    
    for entry in entries:
        day = entry.time_slot.day
        period = entry.time_slot.period
        timetable_grid[day][period] = {
            'subject': entry.subject.code,
            'class': str(entry.class_section),
            'type': 'main',
            'is_lab': entry.is_lab_session
        }
    
    for entry in assistant_entries:
        day = entry.time_slot.day
        period = entry.time_slot.period
        if timetable_grid[day][period] is None:
            timetable_grid[day][period] = {
                'subject': entry.subject.code,
                'class': str(entry.class_section),
                'type': 'assistant',
                'is_lab': entry.is_lab_session
            }
    
    context = {
        'faculty': faculty,
        'timetable_grid': timetable_grid,
        'days': days,
        'periods': periods,
        'config': config,
    }
    return render(request, 'faculty/dashboard.html', context)


@login_required
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


# ============ TIMETABLE VIEWS ============

def timetable_view(request):
    """View timetables (class-wise or faculty-wise)"""
    config = SystemConfiguration.objects.first()
    semester_instance = config.get_semester_instance() if config else '2024-ODD'
    
    view_type = request.GET.get('type', 'class')
    selected_id = request.GET.get('id')
    
    context = {
        'config': config,
        'view_type': view_type,
        'days': ['MON', 'TUE', 'WED', 'THU', 'FRI'],
        'periods': range(1, 8),
    }
    
    if view_type == 'class':
        # Get all classes for selection
        if config and config.active_semester_type == 'ODD':
            classes = ClassSection.objects.filter(
                semester__number__in=[1, 3, 5, 7]
            ).select_related('semester')
        else:
            classes = ClassSection.objects.filter(
                semester__number__in=[2, 4, 6, 8]
            ).select_related('semester')
        
        context['classes'] = classes
        
        if selected_id:
            entries = TimetableEntry.objects.filter(
                class_section_id=selected_id,
                semester_instance=semester_instance
            ).select_related('subject', 'faculty', 'time_slot')
            
            timetable_grid = {day: {p: None for p in range(1, 8)} for day in context['days']}
            
            for entry in entries:
                day = entry.time_slot.day
                period = entry.time_slot.period
                timetable_grid[day][period] = {
                    'subject': entry.subject.code,
                    'subject_name': entry.subject.name,
                    'faculty': entry.faculty.name,
                    'is_lab': entry.is_lab_session,
                    'assistant': entry.assistant_faculty.name if entry.assistant_faculty else None
                }
            
            context['timetable_grid'] = timetable_grid
            context['selected_class'] = ClassSection.objects.get(id=selected_id)
    
    elif view_type == 'faculty':
        faculties = Faculty.objects.filter(is_active=True)
        context['faculties'] = faculties
        
        if selected_id:
            entries = TimetableEntry.objects.filter(
                Q(faculty_id=selected_id) | Q(assistant_faculty_id=selected_id),
                semester_instance=semester_instance
            ).select_related('class_section', 'subject', 'time_slot', 'faculty')
            
            timetable_grid = {day: {p: None for p in range(1, 8)} for day in context['days']}
            
            for entry in entries:
                day = entry.time_slot.day
                period = entry.time_slot.period
                is_assistant = str(entry.assistant_faculty_id) == selected_id
                timetable_grid[day][period] = {
                    'subject': entry.subject.code,
                    'class': str(entry.class_section),
                    'is_lab': entry.is_lab_session,
                    'role': 'Assistant' if is_assistant else 'Main'
                }
            
            context['timetable_grid'] = timetable_grid
            context['selected_faculty'] = Faculty.objects.get(id=selected_id)
    
    return render(request, 'timetable/view.html', context)


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
