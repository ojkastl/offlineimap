# IMAP folder support
# Copyright (C) 2002 John Goerzen
# <jgoerzen@complete.org>
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

from Base import BaseFolder
from offlineimap import imaputil, imaplib
from offlineimap.ui import UIBase
import rfc822, time, string
from StringIO import StringIO
from copy import copy


class IMAPFolder(BaseFolder):
    def __init__(self, imapserver, name, visiblename, accountname, repository):
        self.config = imapserver.config
        self.expunge = 1
        if self.config.has_option(accountname, 'expunge'):
           self.expunge = self.config.getboolean(accountname, 'expunge')
        self.name = imaputil.dequote(name)
        self.root = None # imapserver.root
        self.sep = imapserver.delim
        self.imapserver = imapserver
        self.messagelist = None
        self.visiblename = visiblename
        self.accountname = accountname
        self.repository = repository

    def getaccountname(self):
        return self.accountname

    def suggeststhreads(self):
        return 1

    def waitforthread(self):
        self.imapserver.connectionwait()

    def getcopyinstancelimit(self):
        return 'MSGCOPY_' + self.accountname

    def getvisiblename(self):
        return self.visiblename

    def getuidvalidity(self):
        imapobj = self.imapserver.acquireconnection()
        try:
            # Primes untagged_responses
            imapobj.select(self.getfullname(), readonly = 1)
            return long(imapobj.untagged_responses['UIDVALIDITY'][0])
        finally:
            self.imapserver.releaseconnection(imapobj)
    
    def cachemessagelist(self):
        imapobj = self.imapserver.acquireconnection()
        self.messagelist = {}

        try:
            # Primes untagged_responses
            imapobj.select(self.getfullname(), readonly = 1)
            maxmsgid = long(imapobj.untagged_responses['EXISTS'][0])
            if maxmsgid < 1:
                # No messages; return
                return

            # Now, get the flags and UIDs for these.
            # We could conceivably get rid of maxmsgid and just say
            # '1:*' here.
            response = imapobj.fetch('1:%d' % maxmsgid, '(FLAGS UID)')[1]
        finally:
            self.imapserver.releaseconnection(imapobj)
        for messagestr in response:
            # Discard the message number.
            messagestr = string.split(messagestr, maxsplit = 1)[1]
            options = imaputil.flags2hash(messagestr)
            if not options.has_key('UID'):
                UIBase.getglobalui().warn('No UID in message with options %s' %\
                                          str(options),
                                          minor = 1)
            else:
                uid = long(options['UID'])
                flags = imaputil.flagsimap2maildir(options['FLAGS'])
                self.messagelist[uid] = {'uid': uid, 'flags': flags}

    def getmessagelist(self):
        return self.messagelist

    def getmessage(self, uid):
        imapobj = self.imapserver.acquireconnection()
        try:
            imapobj.select(self.getfullname(), readonly = 1)
            return imapobj.uid('fetch', '%d' % uid, '(BODY.PEEK[])')[1][0][1].replace("\r\n", "\n")
        finally:
            self.imapserver.releaseconnection(imapobj)
    
    def getmessageflags(self, uid):
        return self.messagelist[uid]['flags']
    
    def savemessage(self, uid, content, flags):
        imapobj = self.imapserver.acquireconnection()
        try:
            try:
                imapobj.select(self.getfullname()) # Needed for search
            except imapobj.readonly:
                UIBase.getglobalui().msgtoreadonly(self, uid, content, flags)
                # Return indicating message taken, but no UID assigned.
                # Fudge it.
                return 0
            
            # This backend always assigns a new uid, so the uid arg is ignored.
            # In order to get the new uid, we need to save off the message ID.

            message = rfc822.Message(StringIO(content))
            mid = message.getheader('Message-Id')
            if mid != None:
                mid = imapobj._quote(mid)
            datetuple = rfc822.parsedate(message.getheader('Date'))
            # Will be None if missing or not in a valid format.
            if datetuple == None:
                datetuple = time.localtime()
            try:
                if datetuple[0] < 1981:
                    raise ValueError
                # This could raise a value error if it's not a valid format.
                date = imaplib.Time2Internaldate(datetuple) 
            except ValueError:
                # Argh, sometimes it's a valid format but year is 0102
                # or something.  Argh.  It seems that Time2Internaldate
                # will rause a ValueError if the year is 0102 but not 1902,
                # but some IMAP servers nonetheless choke on 1902.
                date = imaplib.Time2Internaldate(time.localtime())

            if content.find("\r\n") == -1:  # Convert line endings if not already
                content = content.replace("\n", "\r\n")

            assert(imapobj.append(self.getfullname(),
                                       imaputil.flagsmaildir2imap(flags),
                                       date, content)[0] == 'OK')
            # Checkpoint.  Let it write out the messages, etc.
            assert(imapobj.check()[0] == 'OK')
            if mid == None:
                # No message ID in original message -- no sense trying to
                # search for it.
                return 0
            # Now find the UID it got.
            try:
                matchinguids = imapobj.uid('search', None,
                                           '(HEADER Message-Id %s)' % mid)[1][0]
            except imapobj.error:
                # IMAP server doesn't implement search or had a problem.
                return 0
            matchinguids = matchinguids.split(' ')
            if len(matchinguids) != 1 or matchinguids[0] == None:
                return 0
            matchinguids.sort()
            try:
                uid = long(matchinguids[-1])
            except ValueError:
                return 0
            self.messagelist[uid] = {'uid': uid, 'flags': flags}
            return uid
        finally:
            self.imapserver.releaseconnection(imapobj)

    def savemessageflags(self, uid, flags):
        imapobj = self.imapserver.acquireconnection()
        try:
            try:
                imapobj.select(self.getfullname())
            except imapobj.readonly:
                UIBase.getglobalui().flagstoreadonly(self, [uid], flags)
                return
            result = imapobj.uid('store', '%d' % uid, 'FLAGS',
                                 imaputil.flagsmaildir2imap(flags))
            assert result[0] == 'OK', 'Error with store: ' + r[1]
        finally:
            self.imapserver.releaseconnection(imapobj)
        result = result[1][0]
        if not result:
            self.messagelist[uid]['flags'] = flags
        else:
            flags = imaputil.flags2hash(imaputil.imapsplit(result)[1])['FLAGS']
            self.messagelist[uid]['flags'] = imaputil.flagsimap2maildir(flags)

    def addmessageflags(self, uid, flags):
        self.addmessagesflags([uid], flags)

    def addmessagesflags(self, uidlist, flags):
        self.processmessagesflags('+', uidlist, flags)

    def deletemessageflags(self, uid, flags):
        self.deletemessagesflags([uid], flags)

    def deletemessagesflags(self, uidlist, flags):
        self.processmessagesflags('-', uidlist, flags)

    def processmessagesflags(self, operation, uidlist, flags):
        imapobj = self.imapserver.acquireconnection()
        try:
            try:
                imapobj.select(self.getfullname())
            except imapobj.readonly:
                UIBase.getglobalui().flagstoreadonly(self, uidlist, flags)
                return
            r = imapobj.uid('store',
                            imaputil.listjoin(uidlist),
                            operation + 'FLAGS',
                            imaputil.flagsmaildir2imap(flags))
            assert r[0] == 'OK', 'Error with store: ' + r[1]
            r = r[1]
        finally:
            self.imapserver.releaseconnection(imapobj)
        # Some IMAP servers do not always return a result.  Therefore,
        # only update the ones that it talks about, and manually fix
        # the others.
        needupdate = copy(uidlist)
        for result in r:
            if result == None:
                # Compensate for servers that don't return anything from
                # STORE.
                continue
            attributehash = imaputil.flags2hash(imaputil.imapsplit(result)[1])
            if not ('UID' in attributehash and 'FLAGS' in attributehash):
                # Compensate for servers that don't return a UID attribute.
                continue
            flags = attributehash['FLAGS']
            uid = long(attributehash['UID'])
            self.messagelist[uid]['flags'] = imaputil.flagsimap2maildir(flags)
            try:
                needupdate.remove(uid)
            except ValueError:          # Let it slide if it's not in the list
                pass
        for uid in needupdate:
            if operation == '+':
                for flag in flags:
                    if not flag in self.messagelist[uid]['flags']:
                        self.messagelist[uid]['flags'].append(flag)
                    self.messagelist[uid]['flags'].sort()
            elif operation == '-':
                for flag in flags:
                    if flag in self.messagelist[uid]['flags']:
                        self.messagelist[uid]['flags'].remove(flag)

    def deletemessage(self, uid):
        self.deletemessages([uid])

    def deletemessages(self, uidlist):
        # Weed out ones not in self.messagelist
        uidlist = [uid for uid in uidlist if uid in self.messagelist]
        if not len(uidlist):
            return        

        self.addmessagesflags(uidlist, ['T'])
        imapobj = self.imapserver.acquireconnection()
        try:
            try:
                imapobj.select(self.getfullname())
            except imapobj.readonly:
                UIBase.getglobalui().deletereadonly(self, uidlist)
                return
            if self.expunge:
                assert(imapobj.expunge()[0] == 'OK')
        finally:
            self.imapserver.releaseconnection(imapobj)
        for uid in uidlist:
            del self.messagelist[uid]
        
        
