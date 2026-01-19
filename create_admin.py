from django.contrib.auth.models import User

if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@eduplanner.com', 'admin123')
    print('Admin user created')
else:
    print('Admin user already exists')
