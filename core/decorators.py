"""
Role-based access control decorators for EDUPLANNER
"""
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required


def role_required(*roles):
    """
    Decorator to restrict access to specific roles.
    
    Usage examples:
        @role_required('ADMIN')
        @role_required('ADMIN', 'TEACHER')
    
    Args:
        *roles: Variable length argument list of role strings (ADMIN, TEACHER, STUDENT)
    
    Returns:
        Decorator function that wraps the view
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(request, *args, **kwargs):
            # Superusers are always treated as ADMIN
            if request.user.is_superuser:
                if 'ADMIN' in roles:
                    return view_func(request, *args, **kwargs)
                else:
                    messages.error(request, 'Access denied. Admin-only privileges required.')
                    return redirect('home')
            
            # Check if user has a profile
            if not hasattr(request.user, 'profile'):
                messages.error(request, 'User profile not found. Please contact administrator.')
                return redirect('home')
            
            # Check if user's role matches required roles
            if request.user.profile.role in roles:
                return view_func(request, *args, **kwargs)
            else:
                messages.error(request, 'Access denied. Insufficient permissions.')
                return redirect('home')
        
        return wrapped_view
    return decorator


def admin_required(view_func):
    """
    Shorthand decorator for admin-only views.
    Equivalent to @role_required('ADMIN')
    """
    return role_required('ADMIN')(view_func)


def teacher_required(view_func):
    """
    Shorthand decorator for teacher-only views.
    Equivalent to @role_required('TEACHER')
    """
    return role_required('TEACHER')(view_func)


def student_required(view_func):
    """
    Shorthand decorator for student-only views.
    Equivalent to @role_required('STUDENT')
    """
    return role_required('STUDENT')(view_func)
