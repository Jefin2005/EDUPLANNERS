"""
AI Assistant Service for EDUPLANNER Admin

Smart rule-based assistant providing:
- Faculty clash detection
- Faculty suggestions for subjects
- Workload monitoring
- System health overview
- Entity search
"""

from django.db.models import Q, Count, Sum, F
from collections import defaultdict

from .models import (
    Department, Semester, ClassSection, Faculty, Subject,
    FacultySubjectAssignment, TimeSlot, TimetableEntry, SystemConfiguration
)


def check_faculty_clashes(faculty_id, time_slot_id=None, day=None):
    """
    Check if a faculty member has any scheduling clashes.
    
    Args:
        faculty_id: ID of the faculty to check
        time_slot_id: Specific time slot to check (optional)
        day: Day code like 'MON' to check all slots on that day (optional)
    
    Returns:
        dict with clash info
    """
    faculty = Faculty.objects.filter(id=faculty_id).select_related('department').first()
    if not faculty:
        return {'error': 'Faculty not found', 'clashes': []}
    
    # Get all timetable entries for this faculty (as main or assistant)
    entries = TimetableEntry.objects.filter(
        Q(faculty_id=faculty_id) | Q(assistant_faculty_id=faculty_id)
    ).select_related('time_slot', 'subject', 'class_section', 'class_section__semester', 'class_section__semester__department')
    
    if time_slot_id:
        # Check if this specific slot is occupied
        slot_entries = entries.filter(time_slot_id=time_slot_id)
        if slot_entries.exists():
            clashes = []
            for entry in slot_entries:
                clashes.append({
                    'subject': entry.subject.name,
                    'subject_code': entry.subject.code,
                    'class': str(entry.class_section),
                    'department': entry.class_section.semester.department.code,
                    'day': entry.time_slot.get_day_display(),
                    'period': entry.time_slot.period,
                    'time': f"{entry.time_slot.start_time.strftime('%H:%M')} - {entry.time_slot.end_time.strftime('%H:%M')}",
                    'is_lab': entry.is_lab_session,
                    'role': 'Assistant' if entry.assistant_faculty_id == faculty_id else 'Main',
                })
            return {
                'has_clash': True,
                'faculty_name': faculty.name,
                'clashes': clashes,
                'message': f"⚠️ {faculty.name} is already booked at this time slot!"
            }
        else:
            slot = TimeSlot.objects.filter(id=time_slot_id).first()
            return {
                'has_clash': False,
                'faculty_name': faculty.name,
                'clashes': [],
                'message': f"✅ {faculty.name} is available at {slot.get_day_display()} Period {slot.period}" if slot else "✅ Available"
            }
    
    if day:
        entries = entries.filter(time_slot__day=day)
    
    # Build full schedule for the faculty
    schedule = defaultdict(list)
    for entry in entries:
        schedule[entry.time_slot.day].append({
            'period': entry.time_slot.period,
            'subject': entry.subject.name,
            'subject_code': entry.subject.code,
            'class': str(entry.class_section),
            'time': f"{entry.time_slot.start_time.strftime('%H:%M')} - {entry.time_slot.end_time.strftime('%H:%M')}",
            'is_lab': entry.is_lab_session,
        })
    
    # Detect double-bookings (same day, same period, different classes)
    clashes = []
    for day_code, day_entries in schedule.items():
        period_map = defaultdict(list)
        for e in day_entries:
            period_map[e['period']].append(e)
        for period, bookings in period_map.items():
            if len(bookings) > 1:
                clashes.append({
                    'day': dict(TimeSlot.DAY_CHOICES).get(day_code, day_code),
                    'period': period,
                    'bookings': bookings
                })
    
    return {
        'has_clash': len(clashes) > 0,
        'faculty_name': faculty.name,
        'schedule': dict(schedule),
        'clashes': clashes,
        'total_entries': entries.count(),
        'message': f"⚠️ Found {len(clashes)} clash(es)!" if clashes else f"✅ No clashes found for {faculty.name}"
    }


def suggest_faculty_for_subject(subject_id, class_section_id=None):
    """
    Suggest best faculty for a subject based on history, workload, and department match.
    
    Returns ranked list of faculty with scores.
    """
    subject = Subject.objects.filter(id=subject_id).select_related('department', 'semester').first()
    if not subject:
        return {'error': 'Subject not found', 'suggestions': []}
    
    # Get all active faculty
    faculty_qs = Faculty.objects.filter(is_active=True).select_related('department')
    
    suggestions = []
    for fac in faculty_qs:
        score = 0
        reasons = []
        
        # 1. Department match (+30 points)
        if fac.department_id == subject.department_id:
            score += 30
            reasons.append('Same department')
        
        # 2. Previous assignment history (+25 points)
        past_assignments = FacultySubjectAssignment.objects.filter(
            faculty=fac, subject=subject
        ).count()
        if past_assignments > 0:
            score += 25
            reasons.append(f'Taught before ({past_assignments}x)')
        
        # 3. Preference match (+20 points)
        if fac.preferences:
            prefs = [p.strip().lower() for p in fac.preferences.split(',')]
            if subject.code.lower() in prefs or subject.name.lower() in prefs:
                score += 20
                reasons.append('Listed in preferences')
            # Partial preference match
            elif any(subject.name.lower().find(p) >= 0 for p in prefs if len(p) > 2):
                score += 10
                reasons.append('Partial preference match')
        
        # 4. Workload availability (+15 points)
        available = fac.available_hours
        if available is None:
            available = fac.max_hours - fac.current_workload
        
        hours_needed = subject.hours_per_week
        if available >= hours_needed:
            score += 15
            reasons.append(f'{available}hrs available')
        elif available > 0:
            score += 5
            reasons.append(f'Only {available}hrs available (needs {hours_needed})')
        else:
            score -= 20
            reasons.append('⚠️ No available hours')
        
        # 5. Designation bonus (Professors get slight preference for theory)
        if subject.subject_type == 'THEORY' and fac.designation == 'PROFESSOR':
            score += 5
            reasons.append('Professor for theory')
        
        if score > 0 or fac.department_id == subject.department_id:
            workload_pct = round((fac.current_workload / fac.max_hours * 100) if fac.max_hours > 0 else 0)
            workload_status = 'green' if workload_pct < 70 else ('yellow' if workload_pct < 90 else 'red')
            
            suggestions.append({
                'id': fac.id,
                'name': fac.name,
                'department': fac.department.code if fac.department else 'N/A',
                'designation': fac.get_designation_display() if hasattr(fac, 'get_designation_display') else fac.designation,
                'score': score,
                'reasons': reasons,
                'workload_current': fac.current_workload,
                'workload_max': fac.max_hours,
                'workload_pct': workload_pct,
                'workload_status': workload_status,
            })
    
    suggestions.sort(key=lambda x: x['score'], reverse=True)
    
    return {
        'subject': {
            'code': subject.code,
            'name': subject.name,
            'type': subject.subject_type,
            'hours': subject.hours_per_week,
            'department': subject.department.code,
            'semester': subject.semester.number,
        },
        'suggestions': suggestions[:10],  # Top 10
        'total_candidates': len(suggestions),
    }


def get_workload_status(faculty_id=None, department_code=None):
    """
    Get workload status for a faculty member or all faculty in a department.
    """
    if faculty_id:
        faculty = Faculty.objects.filter(id=faculty_id).select_related('department').first()
        if not faculty:
            return {'error': 'Faculty not found'}
        
        current = faculty.current_workload
        maximum = faculty.max_hours
        pct = round((current / maximum * 100) if maximum > 0 else 0)
        status = 'green' if pct < 70 else ('yellow' if pct < 90 else 'red')
        
        # Get breakdown by subject
        entries = TimetableEntry.objects.filter(
            Q(faculty_id=faculty_id) | Q(assistant_faculty_id=faculty_id)
        ).select_related('subject').values('subject__code', 'subject__name').annotate(
            hours=Count('id')
        )
        
        return {
            'faculty_name': faculty.name,
            'department': faculty.department.code if faculty.department else 'N/A',
            'current_hours': current,
            'max_hours': maximum,
            'available_hours': maximum - current,
            'percentage': pct,
            'status': status,
            'status_label': {'green': 'Available', 'yellow': 'Busy', 'red': 'Overloaded'}[status],
            'breakdown': list(entries),
        }
    
    if department_code:
        faculty_qs = Faculty.objects.filter(
            department__code=department_code, is_active=True
        ).select_related('department')
    else:
        faculty_qs = Faculty.objects.filter(is_active=True).select_related('department')
    
    results = []
    for fac in faculty_qs:
        current = fac.current_workload
        maximum = fac.max_hours
        pct = round((current / maximum * 100) if maximum > 0 else 0)
        status = 'green' if pct < 70 else ('yellow' if pct < 90 else 'red')
        
        results.append({
            'id': fac.id,
            'name': fac.name,
            'department': fac.department.code if fac.department else 'N/A',
            'current_hours': current,
            'max_hours': maximum,
            'available_hours': maximum - current,
            'percentage': pct,
            'status': status,
        })
    
    results.sort(key=lambda x: x['percentage'], reverse=True)
    
    overloaded = sum(1 for r in results if r['status'] == 'red')
    busy = sum(1 for r in results if r['status'] == 'yellow')
    
    return {
        'faculty': results,
        'summary': {
            'total': len(results),
            'overloaded': overloaded,
            'busy': busy,
            'available': len(results) - overloaded - busy,
        }
    }


def get_system_health():
    """
    Get a comprehensive system health overview for the admin.
    """
    config = SystemConfiguration.objects.first()
    
    # Counts
    total_depts = Department.objects.filter(is_active=True).count()
    total_faculty = Faculty.objects.filter(is_active=True).count()
    total_subjects = Subject.objects.count()
    total_classes = ClassSection.objects.count()
    total_entries = TimetableEntry.objects.count()
    total_slots = TimeSlot.objects.count()
    
    # Faculty workload analysis
    overloaded_faculty = []
    idle_faculty = []
    for fac in Faculty.objects.filter(is_active=True).select_related('department'):
        current = fac.current_workload
        maximum = fac.max_hours
        if maximum > 0:
            pct = (current / maximum) * 100
            if pct >= 90:
                overloaded_faculty.append({
                    'name': fac.name,
                    'department': fac.department.code if fac.department else 'N/A',
                    'workload': f"{current}/{maximum}hrs ({round(pct)}%)"
                })
            elif current == 0:
                idle_faculty.append({
                    'name': fac.name,
                    'department': fac.department.code if fac.department else 'N/A',
                })
    
    # Unassigned subjects (subjects with no timetable entries)
    assigned_subject_ids = TimetableEntry.objects.values_list('subject_id', flat=True).distinct()
    unassigned_subjects = Subject.objects.exclude(
        id__in=assigned_subject_ids
    ).select_related('department', 'semester')[:20]
    
    unassigned_list = [{
        'code': s.code,
        'name': s.name,
        'department': s.department.code,
        'semester': s.semester.number,
    } for s in unassigned_subjects]
    
    # Detect faculty clashes across all entries
    clash_count = 0
    all_entries = TimetableEntry.objects.all().values('faculty_id', 'time_slot_id')
    slot_faculty_map = defaultdict(list)
    for entry in all_entries:
        slot_faculty_map[(entry['faculty_id'], entry['time_slot_id'])].append(entry)
    
    # Check for faculty teaching multiple classes at same time
    faculty_slot_map = defaultdict(set)
    for entry in TimetableEntry.objects.all().values('faculty_id', 'time_slot_id', 'class_section_id'):
        key = (entry['faculty_id'], entry['time_slot_id'])
        faculty_slot_map[key].add(entry['class_section_id'])
    
    clash_details = []
    for (fac_id, slot_id), class_ids in faculty_slot_map.items():
        if len(class_ids) > 1:
            clash_count += 1
            fac = Faculty.objects.filter(id=fac_id).first()
            slot = TimeSlot.objects.filter(id=slot_id).first()
            if fac and slot:
                clash_details.append({
                    'faculty': fac.name,
                    'day': slot.get_day_display(),
                    'period': slot.period,
                    'classes_count': len(class_ids),
                })
    
    # Health score calculation (0-100)
    health_score = 100
    if clash_count > 0:
        health_score -= min(40, clash_count * 10)
    if len(overloaded_faculty) > 0:
        health_score -= min(20, len(overloaded_faculty) * 5)
    if len(unassigned_list) > 5:
        health_score -= min(20, len(unassigned_list) * 2)
    if total_entries == 0:
        health_score -= 20
    health_score = max(0, health_score)
    
    health_label = 'Excellent' if health_score >= 80 else ('Good' if health_score >= 60 else ('Fair' if health_score >= 40 else 'Critical'))
    health_color = 'green' if health_score >= 80 else ('yellow' if health_score >= 60 else ('orange' if health_score >= 40 else 'red'))
    
    return {
        'score': health_score,
        'label': health_label,
        'color': health_color,
        'counts': {
            'departments': total_depts,
            'faculty': total_faculty,
            'subjects': total_subjects,
            'classes': total_classes,
            'timetable_entries': total_entries,
            'time_slots': total_slots,
        },
        'issues': {
            'clashes': clash_count,
            'clash_details': clash_details[:5],
            'overloaded_faculty': overloaded_faculty,
            'idle_faculty': idle_faculty[:10],
            'unassigned_subjects': unassigned_list,
        },
        'semester_mode': config.active_semester_type if config else 'N/A',
    }


def search_entities(query):
    """
    Quick fuzzy search across faculty, subjects, departments, and classes.
    """
    if not query or len(query) < 2:
        return {'results': []}
    
    results = []
    q = query.strip()
    
    # Search faculty
    for fac in Faculty.objects.filter(
        Q(name__icontains=q) | Q(email__icontains=q)
    ).select_related('department')[:5]:
        results.append({
            'type': 'faculty',
            'icon': 'bi-person',
            'name': fac.name,
            'detail': f"{fac.department.code if fac.department else 'N/A'} · {fac.designation}",
            'id': fac.id,
        })
    
    # Search subjects
    for sub in Subject.objects.filter(
        Q(code__icontains=q) | Q(name__icontains=q) | Q(short_code__icontains=q)
    ).select_related('department', 'semester')[:5]:
        results.append({
            'type': 'subject',
            'icon': 'bi-book',
            'name': f"{sub.code} — {sub.name}",
            'detail': f"{sub.department.code} · S{sub.semester.number} · {sub.subject_type}",
            'id': sub.id,
        })
    
    # Search departments
    for dept in Department.objects.filter(
        Q(code__icontains=q) | Q(name__icontains=q)
    )[:3]:
        results.append({
            'type': 'department',
            'icon': 'bi-building',
            'name': f"{dept.code} — {dept.name}",
            'detail': 'Department',
            'id': dept.id,
        })
    
    # Search classes
    for cls in ClassSection.objects.filter(
        name__icontains=q
    ).select_related('semester', 'semester__department')[:3]:
        results.append({
            'type': 'class',
            'icon': 'bi-door-open',
            'name': str(cls),
            'detail': f"{cls.semester.department.code} · S{cls.semester.number}",
            'id': cls.id,
        })
    
    return {
        'query': q,
        'results': results,
        'count': len(results),
    }


def validate_assignment(faculty_id, subject_id, class_section_id, time_slot_id):
    """
    All-in-one validation for a proposed timetable assignment.
    Checks clashes, workload, and department match.
    """
    issues = []
    warnings = []
    
    faculty = Faculty.objects.filter(id=faculty_id).select_related('department').first()
    subject = Subject.objects.filter(id=subject_id).select_related('department').first()
    class_section = ClassSection.objects.filter(id=class_section_id).select_related('semester__department').first()
    time_slot = TimeSlot.objects.filter(id=time_slot_id).first()
    
    if not all([faculty, subject, class_section, time_slot]):
        return {'valid': False, 'issues': ['One or more entities not found'], 'warnings': []}
    
    # 1. Faculty clash check
    existing = TimetableEntry.objects.filter(
        Q(faculty_id=faculty_id) | Q(assistant_faculty_id=faculty_id),
        time_slot_id=time_slot_id
    ).select_related('class_section', 'subject')
    
    if existing.exists():
        for entry in existing:
            issues.append(
                f"Faculty {faculty.name} is already teaching {entry.subject.code} for {entry.class_section} at this time"
            )
    
    # 2. Class section clash check
    class_existing = TimetableEntry.objects.filter(
        class_section_id=class_section_id,
        time_slot_id=time_slot_id
    ).select_related('subject')
    
    if class_existing.exists():
        for entry in class_existing:
            issues.append(
                f"Class {class_section} already has {entry.subject.code} scheduled at this time"
            )
    
    # 3. Workload check
    current = faculty.current_workload
    maximum = faculty.max_hours
    if current >= maximum:
        issues.append(f"Faculty {faculty.name} has reached max workload ({current}/{maximum} hrs)")
    elif current >= maximum * 0.9:
        warnings.append(f"Faculty {faculty.name} is nearly at capacity ({current}/{maximum} hrs)")
    
    # 4. Department match
    if faculty.department_id != subject.department_id:
        # Not necessarily an error — BS faculty teach across departments
        warnings.append(f"Faculty dept ({faculty.department.code if faculty.department else 'N/A'}) differs from subject dept ({subject.department.code})")
    
    return {
        'valid': len(issues) == 0,
        'issues': issues,
        'warnings': warnings,
        'summary': {
            'faculty': faculty.name,
            'subject': f"{subject.code} — {subject.name}",
            'class': str(class_section),
            'time': f"{time_slot.get_day_display()} P{time_slot.period}",
        }
    }
