# -*- coding: utf-8 -*-
# Generated by Django 1.11.2 on 2017-12-08 08:24
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locking', '0001_squashed_0007_auto_20171004_0900'),
    ]

    operations = [
        migrations.AlterField(
            model_name='nonblockinglock',
            name='expires_on',
            field=models.DateTimeField(db_index=True, verbose_name='expires on'),
        ),
        migrations.AlterField(
            model_name='nonblockinglock',
            name='renewed_on',
            field=models.DateTimeField(db_index=True, verbose_name='renewed on'),
        ),
    ]
