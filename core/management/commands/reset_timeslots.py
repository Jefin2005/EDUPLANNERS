from django.core.management.base import BaseCommand
from core.models import TimeSlot, TimetableEntry
from datetime import time


class Command(BaseCommand):
    help = 'Delete all existing time slots and timetable entries, then create new ones with updated schedule'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Starting time slot reset...'))
        
        # Delete all timetable entries first
        timetable_count = TimetableEntry.objects.count()
        if timetable_count > 0:
            TimetableEntry.objects.all().delete()
            self.stdout.write(self.style.SUCCESS(f'Deleted {timetable_count} timetable entries'))
        else:
            self.stdout.write(self.style.NOTICE('No timetable entries to delete'))
        
        # Delete all time slots
        slot_count = TimeSlot.objects.count()
        if slot_count > 0:
            TimeSlot.objects.all().delete()
            self.stdout.write(self.style.SUCCESS(f'Deleted {slot_count} old time slots'))
        else:
            self.stdout.write(self.style.NOTICE('No time slots to delete'))
        
        # Create new time slots
        self._create_time_slots()
        
        new_slot_count = TimeSlot.objects.count()
        teaching_count = TimeSlot.objects.filter(slot_type__in=['MORNING', 'AFTERNOON']).count()
        non_teaching_count = new_slot_count - teaching_count
        
        self.stdout.write(self.style.SUCCESS(f'\nCreated {new_slot_count} new time slots'))
        self.stdout.write(self.style.SUCCESS(f'  - {teaching_count} teaching slots'))
        self.stdout.write(self.style.SUCCESS(f'  - {non_teaching_count} non-teaching slots (lunch + recess)'))
        self.stdout.write(self.style.SUCCESS('\nTime slots reset complete!'))
    
    def _create_time_slots(self):
        """Create standard time slot configuration"""
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
