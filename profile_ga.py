import os
import django

from core.genetic_algorithm import generate_department_timetable

def run_profiler():
    dept_id = 3
    print(f"Calling generate_department_timetable for department ({dept_id})")
    timetable = generate_department_timetable(dept_id, "2023-2024")
    print("Done calling")

run_profiler()
