"""
AI Chat Service for EDUPLANNER Admin

LLM-powered conversational assistant using Google Gemini.
Provides natural language interface for admin operations:
- Query faculty workloads, schedules, clashes
- Search for subjects, faculty, classes
- Get recommendations and insights
- Execute validated data operations (with confirmation)

Gracefully degrades when no API key is configured.
"""

import json
import logging
from django.conf import settings

from .models import (
    Department, Semester, ClassSection, Faculty, Subject,
    FacultySubjectAssignment, TimeSlot, TimetableEntry, SystemConfiguration
)
from . import ai_assistant

logger = logging.getLogger(__name__)


def get_gemini_client():
    """
    Get the Gemini generative AI client. Returns (client, model_name) tuple or (None, None).
    Tries the new google.genai SDK first, then falls back to the legacy SDK.
    """
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        return None
    
    model_name = getattr(settings, 'GEMINI_MODEL', 'gemini-2.0-flash')
    
    # Try new SDK first (google-genai)
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        return ('new', client, model_name)
    except ImportError:
        pass
    
    # Fallback to legacy SDK (google-generativeai)
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        return ('legacy', model, model_name)
    except ImportError:
        logger.warning("No Gemini SDK installed. Run: pip3 install google-genai")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize Gemini: {e}")
        return None


def build_system_prompt():
    """
    Build the system prompt with current database context.
    """
    config = SystemConfiguration.objects.first()
    
    # Get summary stats
    dept_count = Department.objects.filter(is_active=True).count()
    faculty_count = Faculty.objects.filter(is_active=True).count()
    subject_count = Subject.objects.count()
    class_count = ClassSection.objects.count()
    entry_count = TimetableEntry.objects.count()
    
    # Get department list
    departments = list(Department.objects.filter(is_active=True).values_list('code', 'name'))
    
    # Get faculty summary (name, dept, workload)
    faculty_summary = []
    for f in Faculty.objects.filter(is_active=True).select_related('department').order_by('name')[:50]:
        faculty_summary.append(
            f"  - {f.name} ({f.department.code if f.department else 'N/A'}) — {f.current_workload}/{f.max_hours}hrs"
        )
    
    prompt = f"""You are the EDUPLANNER AI Assistant, an intelligent helper for academic timetable management.
You assist the admin with queries about faculty, subjects, timetables, and scheduling.

CURRENT DATABASE STATE:
- Semester Mode: {config.active_semester_type if config else 'N/A'}
- Academic Year: {config.current_academic_year if config else 'N/A'}
- Departments ({dept_count}): {', '.join(f'{c} ({n})' for c, n in departments)}
- Active Faculty: {faculty_count}
- Subjects: {subject_count}
- Classes: {class_count}
- Timetable Entries: {entry_count}

FACULTY OVERVIEW:
{chr(10).join(faculty_summary) if faculty_summary else '  No active faculty found.'}

CAPABILITIES:
1. Answer questions about faculty workloads, schedules, and availability
2. Find subjects, classes, and their assignments
3. Detect scheduling clashes and conflicts
4. Recommend faculty for subjects based on department, history, and workload
5. Provide system health overviews

RULES:
- Be concise but thorough. Use bullet points for lists.
- When mentioning faculty or subjects, include relevant context (department, hours, etc.)
- If asked to modify data, explain what would happen but note that changes require confirmation.
- Use **bold** for important names and numbers.
- If you don't have enough information, say so clearly.
- Always be helpful and professional.
"""
    return prompt


def build_context_for_query(message):
    """
    Analyze the user's message and fetch relevant context from the database.
    Returns additional context string to append to the prompt.
    """
    msg_lower = message.lower()
    context_parts = []
    
    # If asking about workload
    if any(w in msg_lower for w in ['workload', 'hours', 'overload', 'busy', 'available']):
        # Get detailed workload data
        for dept in Department.objects.filter(is_active=True):
            dept_faculty = Faculty.objects.filter(department=dept, is_active=True)
            if dept_faculty.exists():
                lines = [f"\n{dept.code} Department Faculty Workloads:"]
                for f in dept_faculty:
                    lines.append(f"  - {f.name}: {f.current_workload}/{f.max_hours}hrs ({f.designation})")
                context_parts.append('\n'.join(lines))
    
    # If asking about clashes
    if any(w in msg_lower for w in ['clash', 'conflict', 'overlap', 'double']):
        health = ai_assistant.get_system_health()
        if health['issues']['clashes'] > 0:
            context_parts.append(f"\nCurrent Clashes ({health['issues']['clashes']} total):")
            for c in health['issues']['clash_details']:
                context_parts.append(f"  - {c['faculty']}: {c['day']} P{c['period']} ({c['classes_count']} classes)")
        else:
            context_parts.append("\nNo scheduling clashes detected in the system.")
    
    # If asking about unassigned
    if any(w in msg_lower for w in ['unassigned', 'no faculty', 'missing', 'empty']):
        health = ai_assistant.get_system_health()
        if health['issues']['unassigned_subjects']:
            lines = [f"\nUnassigned Subjects ({len(health['issues']['unassigned_subjects'])} total):"]
            for s in health['issues']['unassigned_subjects']:
                lines.append(f"  - {s['code']} — {s['name']} ({s['department']} S{s['semester']})")
            context_parts.append('\n'.join(lines))
    
    # If asking about specific faculty by name
    import re
    # Try to find faculty names mentioned
    for f in Faculty.objects.filter(is_active=True).select_related('department'):
        name_parts = f.name.lower().split()
        if any(part in msg_lower for part in name_parts if len(part) > 2):
            entries = TimetableEntry.objects.filter(faculty=f).select_related('subject', 'time_slot', 'class_section')
            schedule_lines = [f"\n{f.name}'s Schedule ({f.department.code if f.department else 'N/A'}, {f.designation}):"]
            schedule_lines.append(f"  Workload: {f.current_workload}/{f.max_hours}hrs")
            for e in entries[:15]:
                schedule_lines.append(
                    f"  - {e.time_slot.get_day_display()} P{e.time_slot.period}: {e.subject.code} ({e.class_section})"
                )
            if not entries.exists():
                schedule_lines.append("  No timetable entries")
            context_parts.append('\n'.join(schedule_lines))
            break  # Only include first match to avoid context overflow
    
    # If asking about health
    if any(w in msg_lower for w in ['health', 'status', 'overview', 'summary']):
        health = ai_assistant.get_system_health()
        context_parts.append(f"""
System Health Score: {health['score']}/100 ({health['label']})
- Faculty Clashes: {health['issues']['clashes']}
- Overloaded Faculty: {len(health['issues']['overloaded_faculty'])}
- Idle Faculty: {len(health['issues']['idle_faculty'])}
- Unassigned Subjects: {len(health['issues']['unassigned_subjects'])}
""")
    
    # If asking about a department
    for dept in Department.objects.filter(is_active=True):
        if dept.code.lower() in msg_lower or dept.name.lower() in msg_lower:
            semesters = Semester.objects.filter(department=dept)
            subjects = Subject.objects.filter(department=dept).select_related('semester')
            faculty = Faculty.objects.filter(department=dept, is_active=True)
            
            context_parts.append(f"""
{dept.code} — {dept.name} Details:
- Semesters: {', '.join(f'S{s.number}' for s in semesters)}
- Subjects: {subjects.count()} ({subjects.filter(subject_type='THEORY').count()} theory, {subjects.filter(subject_type='LAB').count()} lab)
- Faculty: {faculty.count()} active members
""")
            # List subjects
            for s in subjects.order_by('semester__number', 'code')[:20]:
                context_parts.append(f"  S{s.semester.number}: {s.code} — {s.name} ({s.subject_type}, {s.ltp_string})")
            break
    
    return '\n'.join(context_parts) if context_parts else ''


def process_admin_message(message):
    """
    Process an admin's chat message using Gemini or fallback to rule-based.
    
    Returns dict with response and metadata.
    """
    # Try LLM first
    result = get_gemini_client()
    
    if result:
        try:
            system_prompt = build_system_prompt()
            extra_context = build_context_for_query(message)
            
            full_prompt = system_prompt
            if extra_context:
                full_prompt += f"\n\nADDITIONAL CONTEXT FOR THIS QUERY:\n{extra_context}"
            full_prompt += f"\n\nADMIN'S MESSAGE: {message}"
            
            sdk_type, client, model_name = result
            
            if sdk_type == 'new':
                # New google.genai SDK
                response = client.models.generate_content(
                    model=model_name,
                    contents=full_prompt
                )
                response_text = response.text
            else:
                # Legacy google.generativeai SDK
                response = client.generate_content(full_prompt)
                response_text = response.text
            
            return {
                'response': response_text,
                'source': 'gemini',
                'model': model_name,
            }
        except Exception as e:
            error_str = str(e)
            logger.error(f"Gemini API error: {e}")
            
            # Check for quota/rate limit errors
            if '429' in error_str or 'quota' in error_str.lower() or 'ResourceExhausted' in error_str:
                fallback = _fallback_response(message)
                prefix = "⚠️ *Gemini API quota exceeded — using smart fallback mode.*\n\n"
                fallback['response'] = prefix + fallback['response']
                fallback['source'] = 'fallback-quota'
                return fallback
            # Fall through to rule-based for other errors
    
    # Fallback: Rule-based responses
    return _fallback_response(message)


def _fallback_response(message):
    """
    Generate a rule-based response when LLM is not available.
    """
    msg_lower = message.lower()
    
    # Health / Status queries
    if any(w in msg_lower for w in ['health', 'status', 'overview', 'system']):
        health = ai_assistant.get_system_health()
        lines = [
            f"**System Health: {health['score']}/100 ({health['label']})**\n",
            f"📊 **Counts:** {health['counts']['departments']} depts, {health['counts']['faculty']} faculty, "
            f"{health['counts']['subjects']} subjects, {health['counts']['timetable_entries']} entries\n"
        ]
        if health['issues']['clashes'] > 0:
            lines.append(f"⚠️ **{health['issues']['clashes']} faculty clash(es)** detected")
        if health['issues']['overloaded_faculty']:
            lines.append(f"🔴 **{len(health['issues']['overloaded_faculty'])} overloaded** faculty members")
        if health['issues']['unassigned_subjects']:
            lines.append(f"📋 **{len(health['issues']['unassigned_subjects'])} unassigned** subjects")
        if not health['issues']['clashes'] and not health['issues']['overloaded_faculty']:
            lines.append("✅ No critical issues found!")
        
        return {'response': '\n'.join(lines), 'source': 'rule-based'}
    
    # Workload queries
    if any(w in msg_lower for w in ['workload', 'hours', 'busy', 'load']):
        # Check if department-specific
        dept_code = None
        for dept in Department.objects.filter(is_active=True):
            if dept.code.lower() in msg_lower:
                dept_code = dept.code
                break
        
        result = ai_assistant.get_workload_status(department_code=dept_code)
        if 'faculty' in result:
            lines = [f"**Faculty Workload{f' — {dept_code} Department' if dept_code else ''}:**\n"]
            for f in result['faculty']:
                status_icon = '🟢' if f['status'] == 'green' else ('🟡' if f['status'] == 'yellow' else '🔴')
                lines.append(f"{status_icon} **{f['name']}** ({f['department']}): {f['current_hours']}/{f['max_hours']}hrs ({f['percentage']}%)")
            
            s = result['summary']
            lines.append(f"\n📊 **Summary:** {s['available']} available, {s['busy']} busy, {s['overloaded']} overloaded")
            return {'response': '\n'.join(lines), 'source': 'rule-based'}
    
    # Unassigned subjects
    if any(w in msg_lower for w in ['unassigned', 'missing', 'no faculty']):
        health = ai_assistant.get_system_health()
        subjects = health['issues']['unassigned_subjects']
        
        # Filter by even/odd semester if specified
        sem_filter = None
        if 'even' in msg_lower:
            sem_filter = 'even'
            subjects = [s for s in subjects if s['semester'] % 2 == 0]
        elif 'odd' in msg_lower:
            sem_filter = 'odd'
            subjects = [s for s in subjects if s['semester'] % 2 == 1]
        
        # Filter by specific department if mentioned
        for dept in Department.objects.filter(is_active=True):
            if dept.code.lower() in msg_lower:
                subjects = [s for s in subjects if s['department'] == dept.code]
                break
        
        if subjects:
            label = f" ({sem_filter.upper()} semesters)" if sem_filter else ""
            lines = [f"**Unassigned Subjects{label} ({len(subjects)}):**\n"]
            for s in subjects:
                lines.append(f"📘 **{s['code']}** — {s['name']} ({s['department']} S{s['semester']})")
            return {'response': '\n'.join(lines), 'source': 'rule-based'}
        return {'response': '✅ All subjects have timetable entries!', 'source': 'rule-based'}
    
    # Clash queries
    if any(w in msg_lower for w in ['clash', 'conflict', 'overlap']):
        health = ai_assistant.get_system_health()
        if health['issues']['clashes'] > 0:
            lines = [f"⚠️ **{health['issues']['clashes']} Faculty Clashes Detected:**\n"]
            for c in health['issues']['clash_details']:
                lines.append(f"🔴 **{c['faculty']}**: {c['day']} Period {c['period']} — teaching {c['classes_count']} classes simultaneously")
            return {'response': '\n'.join(lines), 'source': 'rule-based'}
        return {'response': '✅ No scheduling clashes detected! The timetable is conflict-free.', 'source': 'rule-based'}
    
    # Search queries
    if any(w in msg_lower for w in ['find', 'search', 'where', 'who teaches', 'show me']):
        # Extract potential search terms (words > 2 chars, not common words)
        stop_words = {'the', 'and', 'for', 'who', 'what', 'where', 'show', 'find', 'search', 'me', 'all', 'can'}
        terms = [w for w in msg_lower.split() if len(w) > 2 and w not in stop_words]
        if terms:
            result = ai_assistant.search_entities(' '.join(terms[:3]))
            if result['results']:
                lines = [f"**Found {result['count']} results:**\n"]
                for r in result['results']:
                    lines.append(f"{'👤' if r['type'] == 'faculty' else '📘' if r['type'] == 'subject' else '🏢'} **{r['name']}** — {r['detail']}")
                return {'response': '\n'.join(lines), 'source': 'rule-based'}
    
    # Add faculty command
    if 'add faculty' in msg_lower or 'add a faculty' in msg_lower:
        return _handle_add_faculty(message)
    
    # Help with add faculty format
    if 'add' in msg_lower and ('faculty' in msg_lower or 'teacher' in msg_lower or 'professor' in msg_lower):
        return {
            'response': (
                "📝 **To add faculty, use this format:**\n\n"
                "`add faculty Name, Department Code, Designation, Email`\n\n"
                "**Example (single):**\n"
                "`add faculty Dr. Rahul Menon, CS, Associate Professor, rahul@college.edu`\n\n"
                "**Example (multiple — separate with ;):**\n"
                "`add faculty Dr. Anu S, CS, Assistant Professor, anu@college.edu; "
                "Mr. Vivek R, ME, Assistant Professor, vivek@college.edu`\n\n"
                "**Valid departments:** " + ", ".join(
                    Department.objects.filter(is_active=True).values_list('code', flat=True)
                ) + "\n"
                "**Valid designations:** Professor, Associate Professor, Assistant Professor"
            ),
            'source': 'rule-based'
        }
    
    # Default response — no LLM available
    return {
        'response': (
            "I can help with these queries:\n\n"
            "📊 **\"Check system health\"** — Get system overview\n"
            "👥 **\"Show workload for CS faculty\"** — Faculty workloads by department\n"
            "📋 **\"What subjects are unassigned?\"** — Find unassigned subjects\n"
            "⚠️ **\"Check clashes\"** — Detect scheduling conflicts\n"
            "🔍 **\"Find [name/code]\"** — Search faculty or subjects\n"
            "➕ **\"Add faculty Name, Dept, Designation, Email\"** — Add one or many faculty\n\n"
            "💡 *For multiple faculty, separate with semicolons (;)*"
        ),
        'source': 'fallback'
    }


def _handle_add_faculty(message):
    """
    Parse and create faculty from a chat command.
    
    Formats supported:
      add faculty Name, DeptCode, Designation, Email
      add faculty Name, DeptCode, Designation  (email auto-generated)
      Multiple entries separated by semicolons (;)
    """
    import re
    
    # Strip the "add faculty" prefix
    raw = re.sub(r'(?i)^add\s+(a\s+)?faculty\s*', '', message).strip()
    
    if not raw:
        return {
            'response': (
                "📝 **Format:** `add faculty Name, Dept, Designation, Email`\n\n"
                "**Example:**\n"
                "`add faculty Dr. Rahul Menon, CS, Associate Professor, rahul@college.edu`\n\n"
                "**Multiple (separate with ;):**\n"
                "`add faculty Dr. Anu S, CS, Assistant Professor, anu@edu.in; Mr. Vivek R, ME, Assistant Professor, vivek@edu.in`"
            ),
            'source': 'rule-based'
        }
    
    # Split by semicolons for bulk add
    entries = [e.strip() for e in raw.split(';') if e.strip()]
    
    # Map designation strings to model choices
    designation_map = {
        'professor': 'PROFESSOR',
        'prof': 'PROFESSOR',
        'associate professor': 'ASSOCIATE_PROFESSOR',
        'assoc prof': 'ASSOCIATE_PROFESSOR',
        'associate prof': 'ASSOCIATE_PROFESSOR',
        'assistant professor': 'ASSISTANT_PROFESSOR',
        'asst prof': 'ASSISTANT_PROFESSOR',
        'assistant prof': 'ASSISTANT_PROFESSOR',
        'ap': 'ASSISTANT_PROFESSOR',
    }
    
    # Get valid departments
    dept_map = {}
    for dept in Department.objects.filter(is_active=True):
        dept_map[dept.code.lower()] = dept
        dept_map[dept.name.lower()] = dept
    
    results = []
    created_count = 0
    error_count = 0
    
    for entry in entries:
        parts = [p.strip() for p in entry.split(',')]
        
        if len(parts) < 3:
            results.append(f"❌ **\"{entry}\"** — Need at least: Name, Department, Designation")
            error_count += 1
            continue
        
        name = parts[0]
        dept_input = parts[1].strip().lower()
        desig_input = parts[2].strip().lower()
        email = parts[3].strip() if len(parts) >= 4 else None
        
        # Validate department
        dept = dept_map.get(dept_input)
        if not dept:
            results.append(f"❌ **{name}** — Unknown department '{parts[1].strip()}'. Valid: {', '.join(d.code for d in dept_map.values())}")
            error_count += 1
            continue
        
        # Validate designation
        designation = designation_map.get(desig_input)
        if not designation:
            results.append(f"❌ **{name}** — Unknown designation '{parts[2].strip()}'. Use: Professor, Associate Professor, or Assistant Professor")
            error_count += 1
            continue
        
        # Auto-generate email if not provided
        if not email:
            slug = name.lower().replace('dr.', '').replace('mr.', '').replace('ms.', '').replace('mrs.', '').strip()
            slug = slug.replace(' ', '.').replace('..', '.')
            email = f"{slug}@college.edu"
        
        # Check if email already exists
        if Faculty.objects.filter(email=email).exists():
            results.append(f"⚠️ **{name}** — Email '{email}' already exists. Skipped.")
            error_count += 1
            continue
        
        # Create the faculty
        try:
            faculty = Faculty.objects.create(
                name=name,
                email=email,
                designation=designation,
                department=dept,
                is_active=True,
            )
            results.append(f"✅ **{faculty.name}** — Added to {dept.code} as {faculty.get_designation_display()} ({email})")
            created_count += 1
        except Exception as e:
            results.append(f"❌ **{name}** — Error: {str(e)}")
            error_count += 1
    
    # Build summary
    summary = f"**Faculty Addition Results:**\n"
    summary += f"✅ {created_count} added"
    if error_count:
        summary += f" · ❌ {error_count} failed"
    summary += "\n\n" + "\n".join(results)
    
    return {'response': summary, 'source': 'rule-based'}
