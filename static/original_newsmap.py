import webbrowser
from Tkinter import *
import tkFont

import urllib

urllib.URLopener.version = "Mozilla/4.0 (compatible; MSIE 5.5; Windows NT 5.0)"

def getHeadlines():
    res = []
    src = urllib.urlopen( 'http://news.google.com/news/en/us/world.html').read()
    # loop through all chunks after an <a class=y href=" occurance
    for chunk in src.split('<a class=y href="')[1:]:
        url, chunk = chunk.split( '">',1)
        headline, chunk = chunk.split( '</a>',1)
        source, chunk = chunk.split( '</b><br>',1)
        description, chunk = chunk.split( '<br>',1)
        res.append( [headline, url, description] )
    return res

root = Tk()
root.title("World News Map")
picture = PhotoImage(file="worldmap.gif")
master = Label(root, image=picture).pack()

keywords = [ line.replace('\n','').split(',') for line in file('keywords.txt') ]

boxes = []

def onClick(event):
    url = event.widget.url
    if not url.startswith('http://'):
         url = 'http://news.google.com' + url
    webbrowser.open( url )
    print event.widget.winfo_geometry()

def findPos( xpix, ypix, wpix, hpix, boxes, tries ):
    for x1,y1,w1,h1 in boxes:
        if (xpix <= x1 <= xpix + wpix) or (x1 <= xpix <= x1+w1):
            if (ypix <= y1 <= ypix + hpix) or (y1 <= ypix <= y1+h1):
                if tries==0: return (None,None)
                return findPos( xpix, ypix-hpix-2, wpix, hpix, boxes, tries-1)
    return (xpix, ypix)

def findMatch( keywords, newsitem ):
    for countryinfo in keywords:
        for kw in countryinfo[2:]:
            if newsitem.lower().find(kw.strip().lower())!=-1:
                return countryinfo
    return None


headlineFont = tkFont.Font(family="Helvetica", size=7)
headlines = getHeadlines()
for caption, url, description in headlines:
    countryinfo = findMatch( keywords, caption + ' '+description )
    if countryinfo:
        xpix = int( (float(countryinfo[0]) /360 ) * 710 )-20
        ypix = int( (float(countryinfo[1]) /180 ) * 400 )
        hpix = headlineFont.metrics('linespace')
        wpix = headlineFont.measure(caption)
        if wpix+xpix>710: xpix=710-wpix
        xpix, ypix = findPos( xpix, ypix, wpix, hpix, boxes, 1   )
        if xpix==None: continue
        boxes.append( [xpix, ypix, wpix, hpix] )
        l = Label(master, text=caption, font=headlineFont )
        l.place( x=xpix,y=ypix )
        l.url = url
        l.bind('<Button-1>', onClick)

root.mainloop()