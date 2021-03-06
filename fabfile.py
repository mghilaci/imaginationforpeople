#!/bin/env python
# -*- coding:utf-8 -*-
from __future__ import with_statement

import os.path
import time

import fabric.operations
from fabric.operations import put, get
from fabric.api import *
from fabric.colors import cyan
from fabric.contrib.files import *


def reloadapp():
    """
    Touch the wsgi
    """
    print(cyan('Reloading the application'))
    venvcmd('touch apache/%(wsginame)s' % env)


def venvcmd(cmd, shell=True, user=None, pty=False, subdir=""):
    if not user:
        user = env.user

    with cd(env.venvfullpath + '/' + env.projectname + '/' + subdir):
        return sudo('source %(venvfullpath)s/bin/activate && ' % env + cmd, shell=shell, user=user, pty=pty)
    

def printenv():
    """
    Print shell env
    """
    venvcmd('env')

## Server scenarios
def commonenv():
    """
    Base environment
    """
    env.venvname = "imaginationforpeople.org"
    env.projectname = "imaginationforpeople"
    env.gitrepo = "ssh://webapp@i4p-dev.imaginationforpeople.org/var/repositories/imaginationforpeople.git"
    env.gitbranch = "master"


def prodenv():
    """
    production environment - Will need some work when moving to seperate server
    """
    commonenv()
    env.venvname = "imaginationforpeople.org"
    env.wsginame = "prod.wsgi"
    env.urlhost = "www.imaginationforpeople.org"
    env.user = "web"
    env.home = "www"
    require('venvname', provided_by=('commonenv',))
    env.hosts = ['i4p-prod.imaginationforpeople.org']

    env.gitbranch = "master"

    env.venvbasepath = os.path.join("/home", env.home, "virtualenvs")
    env.venvfullpath = env.venvbasepath + '/' + env.venvname + '/'
    
    
def stagenv():
    """
    Stagging environment
    """
    commonenv()
    env.wsginame = "staging.wsgi"
    env.urlhost = "staging.imaginationforpeople.org"
    env.user = "webapp"
    env.home = "webapp"
    require('venvname', provided_by=('commonenv',))
    env.hosts = ['i4p-dev.imaginationforpeople.org']

    env.gitrepo = "/var/repositories/imaginationforpeople.git"
    env.gitbranch = "develop"

    env.venvbasepath = os.path.join("/home", env.home, "virtualenvs")
    env.venvfullpath = env.venvbasepath + '/' + env.venvname + '/'


## Virtualenv
def build_virtualenv():
    """
    Build the virtualenv
    """
    print(cyan('Creating a fresh virtualenv'))
    require('venvfullpath', provided_by=('stagenv', 'prodenv'))
    sudo('rm /tmp/distribute* || echo "ok"') # clean after hudson
    run('virtualenv --no-site-packages --distribute %(venvfullpath)s' % env)
    sudo('rm /tmp/distribute* || echo "ok"') # clean after myself
    

def update_requirements():
    """
    update external dependencies on remote host
    """
    print(cyan('Updating requirements using PIP'))
    run("yes w | pip install -E %(venvfullpath)s -Ur %(venvfullpath)s/%(projectname)s/requirements.txt" % env)

def fixperms():
    """
    fix permissions
    """
    with cd(env.venvfullpath):
        # sudo('chown :www-data -R %(projectname)s && chmod g+rw -R %(projectname)s' % env)
        # FIXME: This SUCKS
        pass

## Django
def syncdb():
    print(cyan('Synching Django database'))
    venvcmd('./manage.py syncdb --noinput')
    venvcmd('./manage.py migrate')


def collect_static_files():
    """
    Collect static files such as pictures
    """
    print(cyan('Collecting static files'))
    venvcmd('./manage.py collectstatic --noinput')

def compile_messages():
    """
    Run compile messages and reload the app
    """
    apps = venvcmd('ls -d */', subdir="apps").split("\n")
    cmd = "django-admin.py compilemessages -v0"
    print(cyan('Compiling i18 messages for the following apps: %s' % apps))
    for app in apps:
        appsubdir = 'apps/%s' % app
        cwd = venvcmd('pwd', subdir=appsubdir)
        if exists(cwd + '/locale'):
            print(cyan('\t * %s' % appsubdir))
            venvcmd(cmd, subdir=appsubdir)
    fixperms()
    reloadapp()

def tests():
    """
    Run all tests on remote
    """
    print(cyan('Running TDD tests'))
    venvcmd('./manage.py test')

    print(cyan('Running BDD tests'))
    venvcmd('./manage.py harvest --verbosity=2')


def deploy_bootstrap():
    """
    Deploy the project the first time.
    """
    build_virtualenv()

    print(cyan('Cloning Git repository'))
    with cd(env.venvfullpath):
        run("git clone %(gitrepo)s %(projectname)s" % env)
        with cd("%(projectname)s" % env):
            run("git fetch origin %s" % env.gitbranch)

    fullupdate()

    # Fix perms
    with cd(env.venvfullpath):
        with cd("%(projectname)s" % env):
            run('mkdir media/uploads media/cache static/CACHE media/mugshots -p')
            sudo('chown www-data -R media/uploads media/cache media/mugshots static/CACHE')

def _updatemaincode():
    """
    Private : we don't want people updating the code without running
    tests
    """
    print(cyan('Updating Git repository'))
    with cd(env.venvfullpath + '/%(projectname)s/' % env):
        run('git fetch')
        run('git checkout %s' % env.gitbranch)
        run('git pull origin %s' % env.gitbranch)
    
def fullupdate():
    """
    Full Update the maincode and the deps
    """
    _updatemaincode()
    update_requirements()
    compile_messages()
    syncdb()
    collect_static_files()
    # tests()
    reloadapp()

def update():
    """
    Fast Update : project maincode
    """
    _updatemaincode()
    compile_messages()
    syncdb()
    collect_static_files()
    # tests()
    reloadapp()

## Webserver
def configure_webservers():
    """
    Configure the webserver stack.
    """
    # apache
    print(cyan('Configuring Apache'))
    fullprojectpath = env.venvfullpath + '/%(projectname)s/' % env
    sudo('cp %sapache/%s /etc/apache2/sites-available/%s' % (fullprojectpath, env.urlhost, env.urlhost))
    sudo('a2ensite %s' % env.urlhost)

    # nginx
    print(cyan('Configuring Nginx'))
    sudo('cp %snginx/%s /etc/nginx/sites-available/%s' % (fullprojectpath, env.urlhost, env.urlhost))
    with cd('/etc/nginx/sites-enabled/'):
        sudo('ln -sf ../sites-available/%s .' % env.urlhost)

    # Fix log dir
    check_or_install_logdir()

    
def install_webservers():
    """
    Install the webserver stack
    """
    print(cyan('Installing web servers'))
    sudo('apt-get install apache2-mpm-prefork libapache2-mod-wsgi -y')
    sudo('apt-get install nginx -y')

def reload_webservers():
    """
    Reload the webserver stack.
    """
    print(cyan("Reloading apache"))
    # Apache
    sudo('apache2ctl -k graceful')

    # Nginx
    print(cyan("Reloading nginx"))
    sudo('/etc/init.d/nginx restart')
    
def check_or_install_logdir():
    """
    Make sure the log directory exists and has right mode and ownership
    """
    print(cyan('Installing a log dir'))
    with cd(env.venvfullpath + '/'):
        sudo('mkdir -p logs/ ; chown www-data logs; chmod o+rwx logs ; pwd')


## Database server
def install_database_server():
    """
    Install a postgresql DB
    """
    print(cyan('Installing Postgresql'))
    sudo('apt-get install -y postgresql-8.4 postgresql-8.4')

def setup_database():
    """
    Create a user and a DB for the project
    """
    # FIXME: pg_hba has to be changed by hand (see doc)
    # FIXME: Password has to be set by hand (see doc)
    sudo('createuser %s -D -R -S' % env.projectname, user='postgres')
    sudo('createdb -O%s %s' % (env.projectname, env.projectname), user='postgres')
    
## Server packages    
def install_basetools():
    """
    Install required base tools
    """
    print(cyan('Installing base tools'))
    sudo('apt-get install -y python-virtualenv python-pip')
    sudo('apt-get install -y git mercurial subversion')
    sudo('apt-get install -y gettext')

def install_builddeps():
    """
    Will install commonly needed build deps for pip django virtualenvs.
    """
    print(cyan('Installing compilers and required libraries'))
    sudo('apt-get install -y build-essential python-dev libjpeg62-dev libpng12-dev zlib1g-dev libfreetype6-dev liblcms-dev libpq-dev libxslt1-dev libxml2-dev ruby-compass libfssm-ruby')

def install_devdeps():
    """
    Will install commonly needed developpement dependencies.
    """
    print(cyan('Installing required developpement tools'))
    sudo('ruby-compass libfssm-ruby')

def meta_full_bootstrap():
    """
    For use on new, empty environnements
    """
    sudo('apt-get update')
    install_basetools()
    install_database_server()
    install_webservers()
    install_builddeps()

    deploy_bootstrap()
    if(env.wsginame == 'dev.wsgi'):
        install_devdeps();

    configure_webservers()
    reload_webservers()


def mirror_prod_to_staging():
    """
    Mirror the content of the production server to the staging one
    """
    assert(env.wsginame == 'staging.wsgi')

    # Files
    with cd(os.path.join(env.venvfullpath, env.projectname, 'media')):
        sudo('rm -rf mugshots')
        run('scp -r web@i4p-prod.imaginationforpeople.org:/home/www/virtualenvs/imaginationforpeople.org/imaginationforpeople/media/mugshots .')

        sudo('rm -rf uploads')
        run('scp -r web@i4p-prod.imaginationforpeople.org:/home/www/virtualenvs/imaginationforpeople.org/imaginationforpeople/media/uploads .')

        sudo('rm -rf cache')
        run('scp -r web@i4p-prod.imaginationforpeople.org:/home/www/virtualenvs/imaginationforpeople.org/imaginationforpeople/media/cache .')

        sudo('chown www-data -R mugshots uploads cache')
        sudo('chmod u+rw -R mugshots uploads cache')
    
def dump_database():
    assert(env.wsginame == 'prod.wsgi')
    run('pg_dump -Uimaginationforpeople imaginationforpeople > ~/i4p_db_%s.sql' % time.strftime('%Y%m%d'))

def get_database():
    dump_database()
    with cd(os.path.join('/home', env.home)):
        filename = 'i4p_db_%s.sql' % time.strftime('%Y%m%d')
        compressed_filename = '%s.bz2' % filename
        run('bzip2 -9 -c %s > %s' % (filename, compressed_filename))
        get(compressed_filename, 'current_database.sql.bz2')

def restore_database():
    assert(env.wsginame == 'staging.wsgi')
    put('current_database.sql.bz2', 'current_database.sql.bz2')
    run('bunzip2 -c current_database.sql.bz2 > current_database.sql')
    sudo('sudo su - postgres -c "dropdb imaginationforpeople"')
    sudo('sudo su - postgres -c "createdb -E UNICODE -Ttemplate0 -Oimaginationforpeople imaginationforpeople"')
    run('psql -Uimaginationforpeople imaginationforpeople < ~/current_database.sql')
