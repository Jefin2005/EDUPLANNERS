from django.db import migrations
from django.contrib.auth.models import User

def create_admin_user(apps, schema_editor):
    # Use apps.get_model to be safe with migrations
    User = apps.get_model('auth', 'User')
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser(
            username='admin',
            email='admin@eduplanner.com',
            password='admin123'
        )
        print('  ✓ Superuser "admin" created automatically')
    else:
        print('  ℹ Superuser "admin" already exists')

def remove_admin_user(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    User.objects.filter(username='admin').delete()

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_subject_short_code'),
    ]

    operations = [
        migrations.RunPython(create_admin_user, reverse_code=remove_admin_user),
    ]
