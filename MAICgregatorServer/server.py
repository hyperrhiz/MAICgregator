#!/usr/bin/env python

import urllib
import urllib2
import datetime
import threading
import operator
import logging
import hashlib

import feedparser
import PyRSS2Gen
from BeautifulSoup import BeautifulSoup
from bsddb3.db import *
from dbxml import *
import web
from wsgilog import WsgiLog, LogIO
import textile

from MAICgregator import post
from MAICgregator import whois
from MAICgregator import db
from MAICgregator import smartypants
import config

version = "0.01"

# Home directory for databases
DB_HOME = "data/"

# Flags for environment creation
DB_ENV_CREATE_FLAGS = DB_CREATE | DB_RECOVER | DB_INIT_LOG | DB_INIT_LOCK | DB_INIT_MPOOL | DB_INIT_TXN | DB_THREAD

DB_NAME = "MAICgregator.db"
DB_XML_NAME = "MAICgregator.dbxml"

# Setup REST-like service
# URLs of the form:
# /MAICgregator, /MAICgregator/about
#   * About the service
# /MAICgregator/help
#   * Help text
# /MAICgregator/all/somewhere.edu
#   * Return all information for somewhere.edu
#   Note: "all" is to be determined :-)
# /MAICgregator/DoDBR/somewhere.edu
#   * Return DoDBR information for somewhere.edu
# and so on...

urls = (
    '/', 'index',
    '/statement', 'statement',
    '/help', 'help',
    '/admin', 'adminIndex',
    '/admin/', 'adminIndex',
    '/admin/view', 'adminView',
    '/admin/trustee/view', 'adminTrusteeView',
    '/admin/edit/(.*?)', 'adminEdit',
    '/admin/index', 'adminIndex',
    '/admin/post', 'adminPost',
    '/admin/logout', 'adminLogout',
    '/MAICgregator/TrusteeImage/(.*?)', 'TrusteeImage',
    '/MAICgregator/TrusteeRelationshipSearch/(.*?)', 'TrusteeSearch',
    '/MAICgregator/GoogleNews/(.*?)', 'GoogleNews',
    '/MAICgregator/Aggregate/(.*?)/(.*?)', 'Aggregate',
    '/MAICgregator/feed/rss/(.*?)/(.*?)', 'RSS',
    '/RSS', 'RSSList',
    '/FAQ', 'FAQ',
    '/TrusteeInfo', 'TrusteeInfo',
    '/docs/preferences', 'documentationPreferences',
    '/docs', 'documentation',
    '/docs/install', 'documentationInstall',
    '/download', 'download',
    '/faq', 'FAQ',
    '/MAICgregator/name/(.*?)', 'name'
)

app = web.application(urls, globals())
webDB = web.database(dbn='mysql', db='MAICgregator', user='MAICgregator', pw='violas')
if web.config.get('_session') is None:
    session = web.session.Session(app, web.session.DiskStore('sessions'),  initializer = {'loggedIn': False})
    web.config._session = session
else:
    session = web.config._session

render = web.template.render('templates/', base = 'layout', cache = config.cache)
renderAdmin = web.template.render('templates/', base = 'layoutAdmin', cache = config.cache)

class Log(WsgiLog):
    def __init__(self, application):
        WsgiLog.__init__(
            self,
            application,
            logformat = '%(message)s',
            tofile = True,
            file = config.log_file,
            interval = config.log_interval,
            backups = config.log_backups
        )
        sys.stdout = LogIO(self.logger, logging.INFO)
        sys.stderr = LogIO(self.logger, logging.ERROR)

class index:
    def GET(self):
        results = webDB.select("posts", limit=10, order="datetime DESC")
        posts = ""
        posts += "<div id='posts'>"
        for item in results:
            posts += "<h2>" + item["title"] + "</h2>\n"
            posts += "<div>" + textile.textile(item["content"]) + "</div>\n"
            posts += "<p>Posted on " + str(item["datetime"]) + "</p>\n"
        posts += "</div>"

        return render.index(version, posts)

class adminIndex:

    def GET(self):
        return render.adminIndex(session)

    def POST(self):
        form = web.input()
        result = webDB.query( "select * from users where username = '%s' and password = '%s'" % (form['username'], hashlib.sha256(form['password']).hexdigest()))
        if len(result)>0:
            session.loggedIn = True
            session.username = form['username'] 
        return renderAdmin.adminIndex(session)

class adminLogout:
    def GET(self):
        session.kill()
        web.redirect("/")

class adminPost:
    def GET(self):
        if (session.loggedIn == False):
            web.redirect("/admin/")
        return renderAdmin.adminPost(session, False)

    def POST(self):
        if (session.loggedIn == False):
            web.redirect("/admin/")

        form = web.input()
        sequenceID = webDB.insert("posts", title=form['title'], content=form['content'], datetime=web.SQLLiteral("NOW()"), username=session.username)
        return renderAdmin.adminPost(session, True)

class adminTrusteeView:
    def GET(self):
        if (session.loggedIn == False):
            web.redirect("/admin/")

        process = ProcessSingleton.getProcess()
        whoisStore = process.getWhois()
        schoolName = whoisStore.getSchoolName("cornell.edu")
        schoolData = process.getSchoolData(schoolName) 
        
        output = ""
        urls = schoolData.getTrusteeURLToAddFromModel()
        bios = schoolData.getTrusteeBioToAddFromModel()
        infos = schoolData.getTrusteeInfoToAddFromModel()

        # TODO
        # Need to format better, put in checkboxes to update
        for url in urls:
            output += "\t".join(url) + "\n"
        for bio in bios:
            output += "\t".join(bio) + "\n"
        for info in infos:
            output += "\t".join(info) + "\n"

        return renderAdmin.adminTrusteeView(urls, bios, infos)

class adminView:
    def GET(self):
        if (session.loggedIn == False):
            web.redirect("/admin/")
        results = webDB.select("posts", order="datetime DESC")
        items = []
        for result in results:
            item = []
            item.append(result['pid'])
            item.append(result['title'])
            item.append(result['content'])
            item.append(result['datetime'])
            items.append(item)
        return renderAdmin.adminView(items)

class adminEdit:

    def GET(self, postID):
        if (session.loggedIn == False):
            web.redirect("/admin/")
        dbVars = dict(postID = postID)
        results = webDB.select("posts", dbVars, where="pid = $postID", order="datetime DESC")
        item = []
        for result in results:
            item.append(result['pid'])
            item.append(result['title'])
            item.append(result['content'])
            item.append(result['datetime'])

        return renderAdmin.adminEdit(item)

    def POST(self, postID):
        if (session.loggedIn == False):
            web.redirect("/admin/")
        
        form = web.input()
        print form
        if form.has_key('submitButton'):
            numRows = webDB.update("posts", "pid = " + postID, title=form['title'], content=form['content'], datetime=web.SQLLiteral("NOW()"), username=session.username)
        elif form.has_key('deleteButton'):
            numRows = webDB.delete("posts", where="pid = " + postID)
        web.redirect("/admin/view")

class documentation:
    def GET(self):
        return render.documentation()

class documentationInstall:
    def GET(self):
        return render.documentationInstall()

class documentationPreferences:
    def GET(self):
        return render.documentationPreferences()

class download:
    def GET(self):
        return render.download()

class help:
    def GET(self):
        return render.help(version)

class FAQ:
    def GET(self):
        fp = open('data/FAQ.txt')
        FAQlist = smartypants.smartyPants("".join(fp.readlines()))
        fp.close()
        FAQs = FAQlist.split('#@!')
        FAQs = [FAQ.split('!@#') for FAQ in FAQs]
        return render.FAQ(FAQs)

class RSSList:
    def GET(self):
        whoisStore = whois.WhoisStore()
        schoolNamesList = list(zip(whoisStore.whois.keys(), whoisStore.whois.values()))
        schoolNamesList.sort(key=operator.itemgetter(1))
        
        return render.RSS(schoolNamesList)

class TrusteeInfo:

    def GET(self):
        process = ProcessSingleton.getProcess()
        whoisStore = process.getWhois()
        schoolNamesList = list(zip(whoisStore.whois.keys(), whoisStore.whois.values()))
        schoolNamesList.sort(key=operator.itemgetter(1))
        
        return render.TrusteeInfo(schoolNamesList)

    def POST(self):
        process = ProcessSingleton.getProcess()
        form = web.input()
        print form
        if (form['human'].lower().find("maicgregator") == -1):
            return "NotHuman"

        data = {}
        hostname = form['hostname']
        if (form.has_key('trusteeURL')):
            data['trusteeResource'] = form['trusteeResource']
            data['trusteeURL'] = form['trusteeURL'].strip()
    
        if (form.has_key('trusteeBio')):
            data['trusteeResource'] = form['trusteeResource']
            data['trusteeBio'] = form['trusteeBio'].strip()

        if (form.has_key('trusteeInfo')):
            data['trusteeInfo'] = form['trusteeInfo'].strip()

        process = ProcessSingleton.getProcess()
        whoisStore = process.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        data['schoolName'] = schoolName.replace(" ", "")
        schoolData = process.getSchoolData(schoolName) 
        schoolData.addTrusteeInfo(data)

        return "Done"

class TrusteeImage:
    def GET(self, personName):
        return post.TrusteeImage(personName)

class name:
    def GET(self, hostname):
        whoisStore = whois.WhoisStore()
        return whoisStore.getSchoolName(hostname)

class ProcessBase(object):
    def __init__(self, dbManager = None):
        # Setup the school data object dictionary
        self.dbManager = dbManager
        self.schoolMapping = {}
        self.whoisStore = None

    def getWhois(self):
        if (self.whoisStore == None):
            self.whoisStore = whois.WhoisStore(dbManager = self.dbManager)

        return self.whoisStore

    def getSchoolData(self, schoolName):
        if not (self.schoolMapping.has_key(schoolName)):
            self.schoolMapping[schoolName] = db.SchoolData(schoolName, dbManager = self.dbManager)
        return self.schoolMapping[schoolName]

    def GoogleNewsSearch(self, hostname):
        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)

        #schoolData = db.SchoolData(schoolName)
        schoolData = self.getSchoolData(schoolName)

        # TODO
        # Make this less atomic; allow the ability to return smaller chunks, random bits, etc.
        # This means we need to come up with a REST api, as well as return error messages
        print schoolName + " || MAICgregator server || Getting Google News"
        results = schoolData.getGoogleNews()
        #schoolData.close()
        return results

    def GoogleNewsSearchRSS2(self, hostname):
        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)

        print schoolName + " || MAICgregator server || Getting Google News RSS"
        results = schoolData.getGoogleNews()
        soup = BeautifulSoup(results)

        tables = soup.findAll("table")
        items = []
        for table in tables:
            timestamp = schoolData.schoolMetadata['GoogleNews']['timestamp']
            
            title = "".join(unicode(item) for item in table.a.contents)
            description = unicode(table.findAll("font")[3])
            url = table.a['href']

            item = PyRSS2Gen.RSSItem(title = title,
                    link = url,
                    description = description,
                    guid = PyRSS2Gen.Guid(url),
                    categories = ["Google News"],
                    author = "info@maicgregator.org (%s)" % schoolName,
                    pubDate = datetime.datetime.fromtimestamp(timestamp))
            items.append(item)

        return items

    def TrusteeRelationshipSearch(self, hostname):
        #whoisStore = whois.WhoisStore()
        #schoolName = whoisStore.getSchoolName(hostname)

        #schoolData = db.SchoolData(schoolName)

        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)

        print schoolName + " || MAICgregator server || Getting Trustee data"
        results = schoolData.getTrustees()

        class UpdateImagesThread(threading.Thread):
            def __init__(self, schoolData, dbManager):
                threading.Thread.__init__(self)
                self.schoolData = schoolData
                self.dbManager = dbManager

            def run(self):
                self.schoolData.updateTrusteeImages()
        
        # This seems to work.  What we need to do is:
        # * Make sure that we provide some sort of timestamp that prevents us from checking each time
        # * Make sure that we don't try checking at the same time; setup some sort of "lock" that prevents us from doing so
        updateImagesThread = UpdateImagesThread(schoolData, self.dbManager)
        updateImagesThread.start()

        return results

    def TrusteeImages(self, hostname):
        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)
        
        trusteeImages = schoolData.getTrusteeImagesFromModel()
        
        output = ""
        for image in trusteeImages:
            output += "<img width='200' src='%s'/>" % image

        return output

    def TrusteeRelationshipSearchRSS2(self, hostname):
        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)

        print schoolName + " || MAICgregator server || Getting Trustee RSS data"
        results = schoolData.getTrustees()

        resultList = results.split("\n")
        items = []
        for result in resultList:
            timestamp = schoolData.schoolMetadata['Trustees']['timestamp']
            url = "http://www.google.com/search?&q=" + urllib.quote(result + " trustee")
            item = PyRSS2Gen.RSSItem(title = result,
                    link = url,
                    description = result,
                    guid = PyRSS2Gen.Guid(url),
                    categories = ["Trustee"],
                    pubDate = datetime.datetime.fromtimestamp(timestamp))
            items.append(item)

        return items
       
        return results

    def DoDBR(self, hostname):
        #whoisStore = whois.WhoisStore()
        #schoolName = whoisStore.getSchoolName(hostname)
        #schoolData = db.SchoolData(schoolName)

        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)

        # TODO
        # Make this less atomic; allow the ability to return smaller chunks, random bits, etc.
        # This means we need to come up with a REST api, as well as return error messages
        print schoolName + " || MAICgregator server || Getting DoDBR data"
        
        results = schoolData.getXML()
        
        return results

    def DoDBRRSS2(self, hostname):
        #whoisStore = whois.WhoisStore()
        #schoolName = whoisStore.getSchoolName(hostname)
        #schoolData = db.SchoolData(schoolName)

        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)

        # TODO
        # Make this less atomic; allow the ability to return smaller chunks, random bits, etc.
        # This means we need to come up with a REST api, as well as return error messages
        print schoolName + " || MAICgregator server || Getting DoDBR RSS data"
        results = schoolData.getXML()
        
        resultList = results.split("\n")
        items = []
        for result in resultList:
            timestamp = schoolData.schoolMetadata['XML']['timestamp']
            data = result.split("\t")
            title = data[1]
            type = data[0]
            id = data[2]
            agency = data[3]
            amount = float(data[4])
            item = PyRSS2Gen.RSSItem(title = title,
                    link = "#",
                    description = "%s from the %s in the amount of $%f with id %s" % (type, agency, amount, id),
                    guid = PyRSS2Gen.Guid(title),
                    categories = ["DoD", type],
                    pubDate = datetime.datetime.fromtimestamp(timestamp))
            items.append(item)

        return items

    def PRNewsSearch(self, hostname):
        #whoisStore = whois.WhoisStore()
        #schoolName = whoisStore.getSchoolName(hostname)
        #schoolData = db.SchoolData(schoolName)

        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)

        # TODO
        # Make this less atomic; allow the ability to return smaller chunks, random bits, etc.
        # This means we need to come up with a REST api, as well as return error messages
        print schoolName + " || MAICgregator server || Getting PR data"
        web.header('Content-Encoding', 'utf-8')
        results = u"\n".join(unicode(item, "utf-8") for item in schoolData.getPRNews())
        
        return results

    def PRNewsSearchRSS2(self, hostname):
        #whoisStore = whois.WhoisStore()
        #schoolName = whoisStore.getSchoolName(hostname)
        #schoolData = db.SchoolData(schoolName)

        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)

        # TODO
        # Make this less atomic; allow the ability to return smaller chunks, random bits, etc.
        # This means we need to come up with a REST api, as well as return error messages
        print schoolName + " || MAICgregator server || Getting PR data"
        #web.header('Content-Encoding', 'utf-8')
        #results = u"\n".join(unicode(item, "utf-8") for item in schoolData.getPRNews())
        results = schoolData.getPRNews()
        items = []
        for result in results:
            timestamp = schoolData.schoolMetadata['PRNews']['timestamp']
            soup = BeautifulSoup(result)
            title = soup.a.contents[1].strip()
            description = title
            url = soup.a['href']

            item = PyRSS2Gen.RSSItem(title = title,
                    link = url,
                    description = description,
                    guid = PyRSS2Gen.Guid(url),
                    categories = ["PR News"],
                    author = "info@maicgregator.org (%s)" % schoolName,
                    pubDate = datetime.datetime.fromtimestamp(timestamp))
            items.append(item)

        return items

    def DoDSTTR(self, hostname):
        # Interesting keys to return in our result
        usefulKeys = ["PK_AWARDS", "AGENCY", "CONTRACT", "AWARD_AMT", "PI_NAME", "FIRM", "URL", "PRO_TITLE", "WholeAbstract"]
        # TODO
        # Deal with case when we don't get a school name back
        #whoisStore = whois.WhoisStore()
        #schoolName = whoisStore.getSchoolName(hostname)
        #schoolData = db.SchoolData(schoolName)

        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)

        print schoolName + " || MAICgregator server || Getting STTR data"
        STTRData = schoolData.getSTTR()
        
        output = ""
        for contract in STTRData:
            output += "\t".join(unicode(contract[key], errors='ignore') for key in usefulKeys) + "\n"
        
        output = output.replace("<", "&lt;")
        output = output.replace(">", "&gt;")
        return output

    def DoDSTTRRSS2(self, hostname):
        # Interesting keys to return in our result
        usefulKeys = ["PK_AWARDS", "AGENCY", "CONTRACT", "AWARD_AMT", "PI_NAME", "FIRM", "URL", "PRO_TITLE", "WholeAbstract"]
        # TODO
        # Deal with case when we don't get a school name back
        #whoisStore = whois.WhoisStore()
        #schoolName = whoisStore.getSchoolName(hostname)
        #schoolData = db.SchoolData(schoolName)

        whoisStore = self.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)
        schoolData = self.getSchoolData(schoolName)

        print schoolName + " || MAICgregator server || Getting STTR RSS data"
        STTRData = schoolData.getSTTR()
        
        items = []
        for contract in STTRData:
            timestamp = schoolData.schoolMetadata['STTR']['timestamp']
            title = contract["PRO_TITLE"]
            abstract = contract["WholeAbstract"]
            amount = float(contract["AWARD_AMT"])
            piName = contract["PI_NAME"]
            firm = contract["FIRM"]
            url = contract["URL"]
            agency = contract["AGENCY"]
            
            description = """%f from the %s to %s and %s
            <br/>
            <br/>
            %s""" % (amount, agency, piName, firm, abstract)

            item = PyRSS2Gen.RSSItem(title = title,
                    link = "http://" + url,
                    description = description,
                    guid = PyRSS2Gen.Guid(url),
                    categories = ["DoD", "STTR"],
                                         author = "info@maicgregator.org (%s)" % schoolName,
                    pubDate = datetime.datetime.fromtimestamp(timestamp))
            items.append(item)

        return items
   
class RSS(ProcessBase):
     def GET(self, hostname, params):
        process = ProcessSingleton.getProcess()
        whoisStore = process.getWhois()
        schoolName = whoisStore.getSchoolName(hostname)

        paramsMapping = {'GoogleNewsSearch': 'GoogleNews',
                    'DoDBR': 'XML',
                    'TrusteeRelationshipSearch': 'Trustees',
                    'DoDSTTR': 'STTR',
                    'PRNewsSearch': 'PRNews'}

        title = "MAICgregator feed for %s and data sources %s" % (schoolName, params)
        link = "http://maicgregator.org/MAICgregator/feed/rss" + hostname + "/" + params
        description = "This is an automatically generated RSS feed of information that is pertinent to the military-academic-industrical complex (MAIC) of %s.  Any questions or comments should go to info -at- maicgregator --dot-- org" % schoolName


        paramList = params.split("+")

        latestTimestamp = 0
        items = []
        for param in paramList:
            actualParam = paramsMapping[param]
            timestamp = process.getSchoolData(schoolName).schoolMetadata[actualParam]['timestamp']
            if (timestamp > latestTimestamp):
                latestTimestamp = timestamp
            resultFunction = getattr(process, param + "RSS2")
            results = resultFunction(hostname)
            items.extend(results)
        rss = PyRSS2Gen.RSS2(title = title,
                    link = link,
                    description = description,
                    lastBuildDate = datetime.datetime.fromtimestamp(latestTimestamp),
                    items = items)
        
        return rss.to_xml()

class Aggregate(ProcessBase):

    def GET(self, hostname, params):
        process = ProcessSingleton.getProcess()

        paramList = params.split("+")

        outputString = u"<?xml version=\"1.0\"?>\n"
        outputString += u"<results>\n"
        for param in paramList:
            outputString += u"\t<%s>\n" % param
            resultFunction = getattr(process, param)
            results = resultFunction(hostname)
            outputString += unicode(results)
            outputString += u"\n\t</%s>\n" % param
        outputString += u"</results>\n"

        web.header("Content-Type", "text/xml; charset=utf-8")
        # Simple replacement
        outputString = outputString.replace("&", "&amp;")
        return outputString

class ProcessSingleton(ProcessBase):
    process = None
    def getProcess():
        if ProcessSingleton.process == None:
            ProcessSingleton.process = ProcessBase(dbManager = db.DBManager())
        return ProcessSingleton.process
    getProcess = staticmethod(getProcess)

class statement:
    def GET(self):
        return render.statement()

class process:
    def GET(self, data):
        return data

# Finally, setup our web application

if (config.fastcgi):
    web.wsgi.runwsgi = lambda func, addr=None: web.wsgi.runfcgi(func, addr)

if __name__ == "__main__":
    app.run(Log)
