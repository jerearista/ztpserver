#!/usr/bin/env python
#
# Copyright (c) 2014, Arista Networks, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#  - Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#  - Redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution.
#  - Neither the name of Arista Networks nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL ARISTA NETWORKS
# BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR
# BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN
# IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import asyncore
import imp
import json
import re
import os
import pdb
import random
import subprocess
import string                        #pylint: disable=W0402
import shutil
import smtpd
import time
import thread
import unittest

import BaseHTTPServer

from collections import namedtuple


#pylint: disable=C0103
Response = namedtuple('Response', 'content_type status contents headers')
#pylint: enable=C0103

ZTPS_SERVER = '127.0.0.1'
ZTPS_PORT = 12345

EAPI_SERVER = '127.0.0.1'
EAPI_PORT = 1080

SMTP_SERVER = '127.0.0.1'
SMTP_PORT = 2525

BOOTSTRAP_FILE = 'client/bootstrap'

CLI_LOG = '/tmp/FastCli-log'
EAPI_LOG = '/tmp/eapi-log-%s' % os.getpid()

STARTUP_CONFIG = '/tmp/startup-config-%s' % os.getpid()
RC_EOS = '/tmp/rc.eos-%s' % os.getpid()
BOOT_EXTENSIONS = '/tmp/boot-extensions-%s' % os.getpid()
BOOT_EXTENSIONS_FOLDER = '/tmp/.extensions-%s' % os.getpid()

FLASH = '/tmp'

STATUS_OK = 200
STATUS_CREATED = 201
STATUS_BAD_REQUEST = 400
STATUS_NOT_FOUND = 404
STATUS_CONFLICT = 409

SYSTEM_MAC = '1234567890'

def debug():
    pdb.set_trace()

ztps = None    #pylint: disable=C0103
def start_ztp_server():
    global ztps     #pylint: disable=W0603
    if not ztps:
        ztps = ZTPServer()
        ztps.start()
    else:
        ztps.cleanup()
    return ztps

smtp = None    #pylint: disable=C0103
def start_smtp_server():
    global smtp     #pylint: disable=W0603
    if not smtp:
        smtp = SmtpServer()
        smtp.start()
    return smtp

eapis = None    #pylint: disable=C0103
def start_eapi_server():
    global eapis    #pylint: disable=W0603
    if not eapis:
        eapis = EAPIServer()
        eapis.start()
    else:
        eapis.cleanup()
    return eapis

def remove_file(filename):
    try:
        os.remove(filename)
    except OSError:
        pass

def clear_cli_log():
    remove_file(CLI_LOG)

def clear_eapi_log():
    remove_file(EAPI_LOG)

def clear_startup_config():
    remove_file(STARTUP_CONFIG)

def clear_rc_eos():
    remove_file(RC_EOS)

def clear_boot_extensions():
    remove_file(BOOT_EXTENSIONS)
    shutil.rmtree(BOOT_EXTENSIONS_FOLDER, ignore_errors=True)

def clear_logs():
    clear_cli_log()
    clear_eapi_log()

def eapi_log():
    try:
        return [x.strip()
                for x in open(EAPI_LOG, 'r').readlines()]
    except IOError:
        return []

def cli_log():
    try:
        return [x.strip().split('-c ')[ -1 ]
                for x in open(CLI_LOG, 'r').readlines()]
    except IOError:
        return []

def file_log(filename, ignore_string=None):

    try:
        lines = [x.strip() for x in open(filename, 'r').readlines()]
        if ignore_string:
            return [y for y in lines if y and ignore_string not in y]
        else:
            return [y for y in lines if y]
    except IOError:
        return []

def get_action(action):
    return open('actions/%s' % action, 'r').read()

def startup_config_action(lines=None):
    if not lines:
        lines = ['test']

    user = os.getenv('USER')
    return '''#!/usr/bin/env python
import os
import pwd

def main(attributes):
   user = pwd.getpwnam('%s').pw_uid
   group = pwd.getpwnam('%s').pw_gid

   f = file('%s', 'w')
   f.write(\'\'\'%s\'\'\')
   f.close()

   os.chmod('%s', 0777)
   os.chown('%s', user, group)
''' % (user, user, STARTUP_CONFIG, '\n'.join(lines),
       STARTUP_CONFIG, STARTUP_CONFIG)

def print_action(msg='TEST', use_attribute=False, create_copy=False):
    #pylint: disable=E0602
    if use_attribute and create_copy:
        return '''#!/usr/bin/env python

def main(attributes):
   attrs = attributes.copy()
   print attrs.get('print_action-attr')
'''

    if use_attribute:
        return '''#!/usr/bin/env python

def main(attributes):
   print attributes.get('print_action-attr')
'''
    else:
        return '''#!/usr/bin/env python

def main(attributes):
   print '%s'
''' % msg

def print_attributes_action(attributes):
    #pylint: disable=E0602
    result = '''#!/usr/bin/env python

def main(attributes):
'''
    for attr in attributes:
        result += '    print attributes.get(\'%s\')\n' % attr
    return result

def fail_action():
    return '''#!/usr/bin/env python

def main(attributes):
   return 2
'''

def erroneous_action():
    return '''THIS_IS_NOT_PYTHON'''

def missing_main_action():
    return '''#!/usr/bin/env python'''

def wrong_signature_action():
    return '''#!/usr/bin/env python

def main():
   pass
'''

def exception_action():
    return '''#!/usr/bin/env python

def main(attributes):
   raise Exception
'''

def random_string():
    return ''.join(random.choice(
            string.ascii_uppercase +
            string.digits) for _ in range(random.randint(3, 20)))


class Bootstrap(object):
    #pylint: disable=R0201

    def __init__(self, server=None, eapi_port=None,
                 ztps_default_config=False):
        os.environ['PATH'] += ':%s/test/client' % os.getcwd()

        self.server = server if server else '%s:%s' % (ZTPS_SERVER, ZTPS_PORT)
        self.eapi_port = eapi_port if eapi_port else EAPI_PORT

        self.output = None
        self.error = None
        self.return_code = None
        self.filename = None
        self.module = None

        self.eapi = start_eapi_server()
        self.ztps = start_ztp_server()
        self.smtp = start_smtp_server()

        self.configure()

        if ztps_default_config:
            self.ztps.set_config_response()
            self.ztps.set_node_check_response()

    def configure(self):
        infile = open(BOOTSTRAP_FILE)
        self.filename = '/tmp/bootstrap-%s' % os.getpid()
        outfile = open(self.filename, 'w')

        for line in infile:
            line = line.replace('$SERVER', 'http://%s' % self.server)
            line = line.replace("COMMAND_API_SERVER = 'localhost'",
                                "COMMAND_API_SERVER = 'localhost:%s'" %
                                self.eapi_port)
            line = line.replace("STARTUP_CONFIG = '/mnt/flash/startup-config'",
                                "STARTUP_CONFIG = '%s'" % STARTUP_CONFIG)
            line = line.replace("FLASH = '/mnt/flash'",
                                "FLASH = '%s'" % FLASH)
            line = line.replace("RC_EOS = '/mnt/flash/rc.eos'",
                                "RC_EOS = '%s'" % RC_EOS)
            line = line.replace(
                "BOOT_EXTENSIONS = '/mnt/flash/boot-extensions'",
                "BOOT_EXTENSIONS = '%s'" % BOOT_EXTENSIONS)
            line = line.replace(
                "BOOT_EXTENSIONS_FOLDER = '/mnt/flash/.extensions'",
                "BOOT_EXTENSIONS_FOLDER = '%s'" % BOOT_EXTENSIONS_FOLDER)

           # Reduce HTTP timeout
            if re.match('^HTTP_TIMEOUT', line):
                line = 'HTTP_TIMEOUT = 0.01'

            outfile.write(line)

        infile.close()
        outfile.close()

        os.chmod(self.filename, 0777)
        self.module = imp.load_source('bootstrap', self.filename)

    def end_test(self):
        # Clean up actions
        for url in self.ztps.responses.keys():
            filename = url.split('/')[-1]
            remove_file('/tmp/%s' % filename)
            remove_file('/tmp/%sc' % filename)

        # Clean up log files
        for filename in os.listdir('/tmp'):
            if re.search('^ztps-log-', filename):
                os.remove(os.path.join('/tmp', filename))

        # Clean up bootstrap script
        remove_file(self.filename)
        remove_file('%sc' % self.filename)

        # Clean up logs
        clear_logs()

        # Other
        clear_startup_config()
        clear_rc_eos()
        clear_boot_extensions()

    def start_test(self):
        try:
            proc = subprocess.Popen([self.filename],
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            (self.output, self.error) = proc.communicate()
        finally:
            os.remove(self.filename)

        self.return_code = proc.returncode             #pylint: disable=E1101

    def node_information_collected(self):
        cmds = ['show version',          # Collect system MAC for logging
                'show version',
                'show lldp neighbors']
        return eapi_log()[-3:] == cmds

    def eapi_configured(self):
        cmds = ['configure',
                'username ztps secret ztps-password privilege 15',
                'management api http-commands',
                'no protocol https',
                'protocol http',
                'no shutdown']
        return cli_log()[:6] == cmds

    def eapi_node_information_collected(self):
        return self.eapi_configured() and self.node_information_collected()

    def server_connection_failure(self):
        return self.return_code == 1

    def eapi_failure(self):
        return self.return_code == 2

    def unexpected_response_failure(self):
        return self.return_code == 3

    def node_not_found_failure(self):
        return self.return_code == 4

    def toplogy_check_failure(self):
        return self.return_code == 5

    def action_not_found_failure(self):
        return self.return_code == 6

    def missing_startup_config_failure(self):
        return self.return_code == 7

    def action_failure(self):
        return self.return_code == 8

    def invalid_definition_format(self):
        return self.return_code == 9

    def invalid_definition_location_failure(self):
        return self.return_code == 10

    def success(self):
        return self.return_code == 0


class EAPIServer(object):
    #pylint: disable=C0103,E0213,W0201

    def __init__(self, mac=SYSTEM_MAC, model='',
                 serial_number='', version=''):
        self.mac = mac
        self.model = model
        self.serial_number = serial_number
        self.version = version

    def cleanup(self):
        self.responses = {}

    def start(self):
        thread.start_new_thread(self._run, ())

    def _run(self):

        class EAPIHandler(BaseHTTPServer.BaseHTTPRequestHandler):

            def do_POST(req):
                request = req.rfile.read(int(req.headers.getheader(
                            'content-length')))
                cmds = [x for x in json.loads(request)['params'][1] if x]
                if cmds:
                    open(EAPI_LOG, 'a+b').write('%s\n' % '\n'.join(cmds))

                print 'EAPIServer: responding to request:%s (%s)' % (
                    req.path, ', '.join(cmds))

                req.send_response(STATUS_OK)

                if req.path == '/command-api':
                    req.send_header('Content-type', 'application/json')
                    req.end_headers()
                    if cmds == ['show version']:
                        req.wfile.write(json.dumps(
                                {'result' :
                                 [{'modelName' : self.model,
                                   'version' : self.version,
                                   'serialNumber' : self.serial_number,
                                   'systemMacAddress' : self.mac}]}))
                    elif cmds == ['show lldp neighbors']:
                        req.wfile.write(json.dumps({'result' :
                                                  [{'lldpNeighbors': []}]}))
                    else:
                        req.wfile.write(json.dumps({'result' : []}))
                    print 'EAPIServer: RESPONSE: {}'
                else:
                    print 'EAPIServer: No RESPONSE'

        server_class = BaseHTTPServer.HTTPServer
        httpd = server_class((EAPI_SERVER, EAPI_PORT), EAPIHandler)
        print time.asctime(), 'EAPIServer: Server starts - %s:%s' % (
            EAPI_SERVER, EAPI_PORT)

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            print time.asctime(), 'EAPIServer: Server stops - %s:%s' % (
                EAPI_SERVER, EAPI_PORT)


class ZTPServer(object):
    #pylint: disable=C0103,,E0213

    # { <URL>: ( <CONTNENT-TYPE>, <STATUS>, <RESPONSE> ) }
    responses = {}

    def cleanup(self):
        self.responses = {}

    def set_file_response(self, filename, output,
                            content_type='application/octet-stream',
                            status=STATUS_OK):
        self.responses['/%s' % filename ] = Response(
            content_type, status,
            output, {})

    def set_action_response(self, action, output,
                            content_type='text/x-python',
                            status=STATUS_OK):
        self.responses['/actions/%s' % action ] = Response(
            content_type, status,
            output, {})

    def set_config_response(self, logging=None, xmpp=None,
                            content_type='application/json',
                            status=STATUS_OK):
        response = { 'logging': [],
                     'xmpp': {}
                     }
        if logging:
            response['logging'] = logging

        if xmpp:
            response['xmpp'] = xmpp

        self.responses['/bootstrap/config'] = Response(
            content_type, status,
            json.dumps(response), {})

    def set_node_check_response(self, content_type='text/html',
                                status=None, location=None):
        if status is None:
            status = random.choice([STATUS_CONFLICT,
                                    STATUS_CREATED])

        headers = {}
        if location:
            headers['location'] = location

        self.responses['/nodes'] = Response(
            content_type, status, 
            '', headers)

    def set_bogus_definition_response(self):
        self.responses['/nodes/%s' % SYSTEM_MAC] = Response(
            'application/json', STATUS_OK,
            json.dumps({}), {})

    def set_definition_response(self, node_id=SYSTEM_MAC,
                                name='DEFAULT_DEFINITION',
                                actions=None,
                                content_type='application/json',
                                status=STATUS_OK):
        response = { 'name': name,
                     'actions': [],
                     }
        if actions:
            response['actions'] += actions

        self.responses['/nodes/%s' % node_id] = Response(
            content_type, status,
            json.dumps(response), {})

    def start(self):
        thread.start_new_thread(self._run, ())

    def _run(self):

        class ZTPSHandler(BaseHTTPServer.BaseHTTPRequestHandler):

            @classmethod
            def do_request(cls, req):
                if req.path in self.responses.keys():
                    response = self.responses[req.path]
                    req.send_response(response.status)
                    req.error_content_type = response.content_type

                    req.send_header('Content-type', response.content_type)
                    for name, value in response.headers.iteritems():
                        req.send_header(name, value)

                    req.end_headers()
                    req.wfile.write(response.contents)
                    print 'ZTPS: RESPONSE: (ct=%s, status=%s, output=%s...)' % (
                        response[0],
                        response[1],
                        response[2][:100])
                else:
                    print 'ZTPS: No RESPONSE'

            def do_GET(req):
                print 'ZTPS: responding to GET request:%s' % req.path
                ZTPSHandler.do_request(req)

            def do_POST(req):
                print 'ZTPS: responding to POST request:%s' % req.path
                headers = self.responses['/nodes'].headers
                if 'location' not in headers:
                    length = req.headers.getheader('content-length')
                    node_id = json.loads(req.rfile.read(
                            int(length)))['systemmac']
                    location  = 'http://%s:%s/nodes/%s' % (ZTPS_SERVER, 
                                                           ZTPS_PORT,
                                                           node_id)
                    self.responses['/nodes'].headers['location'] = location

                ZTPSHandler.do_request(req)

        server_class = BaseHTTPServer.HTTPServer
        httpd = server_class((ZTPS_SERVER, ZTPS_PORT), ZTPSHandler)

        print time.asctime(), 'ZTPS: Server starts - %s:%s' % (
            ZTPS_SERVER, ZTPS_PORT)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            print time.asctime(), 'ZTPS: Server stops - %s:%s' % (
                ZTPS_SERVER, ZTPS_PORT)


class SmtpServer(object):
    #pylint: disable=E0211

    def start(self):
        thread.start_new_thread(self._run, ())

    @classmethod
    def _run(cls):

        class SMTPServer(smtpd.SMTPServer):

            def __init__(*args, **kwargs):
                print "SMTP: Running smtp server on port 2525"
                smtpd.SMTPServer.__init__(*args, **kwargs)

            def process_message(*args, **kwargs):
                pass

        smtp_server = SMTPServer((SMTP_SERVER, SMTP_PORT), None)
        print time.asctime(), 'SMTP: Server starts - %s:%s' % (
            SMTP_SERVER, SMTP_PORT)
        try:
            asyncore.loop()
        except KeyboardInterrupt:
            pass
        finally:
            smtp_server.close()
            print time.asctime(), 'SMTPS: Server stops - %s:%s' % (
                SMTP_SERVER, SMTP_PORT)

class ActionFailureTest(unittest.TestCase):
    #pylint: disable=R0904

    def basic_test(self, action, return_code, attributes=None,
                   action_value=None, file_responses=None):
        if not attributes:
            attributes = {}

        if not file_responses:
            file_responses = {}

        if not action_value:
            action_value = get_action(action)

        bootstrap = Bootstrap(ztps_default_config=True)
        bootstrap.ztps.set_definition_response(
            actions=[{'action' : 'test_action',
                      'attributes' : attributes}])
        bootstrap.ztps.set_action_response('test_action',
                                           action_value)

        for key, value in file_responses.iteritems():
            bootstrap.ztps.set_file_response(key, value)            

        bootstrap.start_test()

        try:
            self.failUnless(bootstrap.action_failure())
            msg = [x for x in bootstrap.output.split('\n') if x][-1]
            self.failUnless('return code %s' % return_code in msg)
        except AssertionError as assertion:
            print 'Output: %s' % bootstrap.output
            print 'Error: %s' % bootstrap.error
            raise assertion
        finally:
            bootstrap.end_test()
