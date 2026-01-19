from django.contrib import admin
from .models import (
    Department, Semester, ClassSection, Faculty, Subject,
    FacultySubjectAssignment, TimeSlot, TimetableEntry, SystemConfiguration
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['code', 'name']
    search_fields = ['name', 'code']


@admin.register(Semester)
class SemesterAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'department', 'number', 'semester_type']
    list_filter = ['department', 'number']
    ordering = ['department', 'number']


@admin.register(ClassSection)
class ClassSectionAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'semester', 'name', 'capacity']
    list_filter = ['semester__department', 'semester']
    search_fields = ['name']


@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'designation', 'max_hours', 'current_workload', 'is_active']
    list_filter = ['designation', 'is_active']
    search_fields = ['name', 'email']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'email', 'designation', 'is_active')
        }),
        ('Authentication', {
            'fields': ('user',),
            'classes': ('collapse',)
        }),
        ('Preferences', {
            'fields': ('preferences',),
            'description': 'Enter comma-separated subject codes'
        }),
    )


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'department', 'semester', 'subject_type', 'hours_per_week', 'credits']
    list_filter = ['department', 'semester', 'subject_type']
    search_fields = ['name', 'code']
    ordering = ['semester', 'code']


@admin.register(FacultySubjectAssignment)
class FacultySubjectAssignmentAdmin(admin.ModelAdmin):
    list_display = ['faculty', 'subject', 'class_section', 'semester_instance', 'is_main']
    list_filter = ['semester_instance', 'is_main', 'subject__department']
    search_fields = ['faculty__name', 'subject__code']


@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'day', 'period', 'start_time', 'end_time', 'is_morning']
    list_filter = ['day']
    ordering = ['day', 'period']


@admin.register(TimetableEntry)
class TimetableEntryAdmin(admin.ModelAdmin):
    list_display = ['class_section', 'time_slot', 'subject', 'faculty', 'semester_instance', 'is_lab_session']
    list_filter = ['semester_instance', 'class_section__semester__department', 'class_section', 'is_lab_session']
    search_fields = ['subject__code', 'faculty__name']
    ordering = ['class_section', 'time_slot']


@admin.register(SystemConfiguration)
class SystemConfigurationAdmin(admin.ModelAdmin):
    list_display = ['current_academic_year', 'active_semester_type', 'periods_per_day', 'days_per_week']
    
    def has_add_permission(self, request):
        # Only allow one configuration
        if SystemConfiguration.objects.exists():
            return False
        return True
    
    def has_delete_permission(self, request, obj=None):
        return False


# Customize admin site header
admin.site.site_header = "EDUPLANNER Administration"
admin.site.site_title = "EDUPLANNER"
admin.site.index_title = "Timetable Generator Management"
