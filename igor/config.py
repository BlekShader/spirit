import json, struct, re, subprocess, time, shelve, hashlib, cPickle, os, sys, plistlib, optparse

data = eval('{%s}' % open('configdata.py').read())

cache = shelve.open('config1.cache')

def lookup(sects, off, soff):
    for vmaddr, sectstart, sectend in sects:
        if off >= sectstart and off < sectend:
            val = vmaddr + (off - sectstart) + soff
            break
    return val

def do_binary_kv(syms, sects, stuff, k, v):
    if v[0] in ('-', '+') and v[1] != ' ':
        name = v[1:]
        offs = 0
        z = name.find('+')
        if z != -1:
            offs = eval(name[z+1:])
            name = name[:z]
        addr = syms[name]
        # Data, so even a thumb symbol shouldn't be &1
        if v[0] == '-': addr &= ~1
        addr += offs
        return addr
    elif v == '!':
        off = re.search('\x14[\x14\x00]{256}', stuff).start()
        val = lookup(sects, off, 0)
        val = (val + 4) & ~3
        return val

    bits = v.split(' ')
    sstr = ''
    soff = None
    loose = False
    aligned = False
    startoff = None
    n = 0
    for bit in bits:
        if bit.startswith('='):
            myaddr = syms[bit[1:]] & ~1 
            for vmaddr, sectstart, sectend in sects:
                if vmaddr <= myaddr < vmaddr + (sectend - sectstart):
                    startoff = myaddr - vmaddr + sectstart
                    break
            else:
                raise ValueError('wtf')
        elif bit == '@':
            loose = True
        elif bit == '+':
            soff = n + 1
        elif bit == '-': # ARM or data
            soff = n
        elif bit == '%':
            soff = n
            aligned = True
        elif bit == '..':
            sstr += '.'
            n += 1
        else:
            sstr += re.escape(chr(int(bit, 16)))
            n += 1
    if soff is None:
        raise ValueError('No offset in %s' % (v,))
    if startoff is not None:
        print stuff[startoff:startoff+64].encode('hex')
        offs = list(re.compile(sstr).finditer(stuff, startoff, startoff+64))
    else:
        offs = list(re.finditer(sstr, stuff))
    if len(offs) == 0:
        print repr(sstr)
        raise ValueError('I couldn\'t find %s' % v)
    elif not loose and len(offs) >= 2:
        raise ValueError('I found multiple (%d) %s' % (len(offs), v))
    off = offs[0].start()
    val = lookup(sects, off, soff)
    if aligned and (val & 3):
        raise ValueError('%s is not aligned: %x' % (v, val))
    return val

def get_syms(binary):
    syms = {}
    for line in subprocess.Popen(['nm', '-p', '-m', binary], stdout=subprocess.PIPE).stdout:
        stuff = line[:-1].split(' ')
        name = stuff[-1]
        addr = stuff[0]
        if not addr: continue
        addr = int(addr, 16)
        if stuff[-2] == '[Thumb]':
            addr |= 1
        syms[name] = addr
    return syms

def get_sects(binary):
    fp = open(binary, 'rb')
    magic, cputype, cpusubtype, \
    filetype, filetype, ncmds, sizeofcmds, \
    flags = struct.unpack('IHHIIIII', fp.read(0x1c))
    sects = []
    stuff = ''
    while True:
        xoff = fp.tell()
        if xoff >= sizeofcmds: break
        cmd, clen = struct.unpack('II', fp.read(8))
        if cmd == 1: # LC_SEGMENT
            fp.seek(xoff + 8)
            name = fp.read(16)
            name = name[:name.find('\0')]
            #print name
            fp.seek(xoff + 24)
            vmaddr, vmsize, foff, fsiz = struct.unpack('IIII', fp.read(16))
            q = fp.tell()
            fp.seek(foff)
            k = len(stuff)
            stuff += fp.read(fsiz)
            sects.append((vmaddr, k, len(stuff)))
            fp.seek(q)
        fp.seek(xoff + clen)
    fp.close()
    return sects, stuff

def do_binary(cfg, name):
    d = cfg.get(name)
    if d is None: return
    binary = d['@binary']
    cachekey = hashlib.sha1(cPickle.dumps((d, os.path.getmtime(binary)), cPickle.HIGHEST_PROTOCOL)).digest()
    if cache.has_key(cachekey):
        cached = cache[cachekey]
        d.update(cached)
        return
    
    syms = get_syms(binary)
    sects, stuff = get_sects(binary)

    tocache = {}
    for k, v in d.iteritems():
        if k == '@binary' or not isinstance(v, basestring): continue
        val = do_binary_kv(syms, sects, stuff, k, v)
        d[k] = tocache[k] = val
    cache[cachekey] = tocache

def dolt(d):
    cflags = ''
    for k, v in d.iteritems():
        if not isinstance(k, basestring) or k.startswith('@'): continue
        if isinstance(v, dict):
            cflags += dolt(v)
            continue
        elif isinstance(v, (int, long)):
            v = hex(v)
        elif not isinstance(v, basestring):
            continue
        cflags += ' -DCONFIG_%s=%s' % (k.upper(), v)
    return cflags

def merge(a, b):
    if isinstance(a, dict):
        new = a.copy()
        new.update(b)
        for k in new:
            if a.has_key(k) and b.has_key(k):
                new[k] = merge(a[k], b[k])
        return new
    else:
        return b

def get_data(platform):
    d = data[platform]
    if d.has_key('<'):
        parent = d['<']
        del d['<']
        d = merge(get_data(parent), d)
    return d

def pretty_print(d):
    for (name, d2) in d.items():
        if isinstance(d2, dict):
            for (k, v) in d2.items():
                if isinstance(v, (long, int)):
                    print '%s -> %s: 0x%x' % (name, k, v)

def go(platform):
    d = get_data(platform)
    do_binary(d, 'kern')
    do_binary(d, 'launchd')
    if verbose:
        pretty_print(d)
    open('config.json', 'w').write(json.dumps(d)+'\n')
    cflags = dolt(d) + '\n'
    open('config.cflags', 'w').write(cflags)
    # Write a trivial map for testing
    plistlib.writePlist({platform: 'igor/one.dylib'}, 'map.plist')

parser = optparse.OptionParser()
parser.add_option('-v', '--verbose', action='store_true', dest='verbose', default=False)
(options, args) = parser.parse_args()
verbose = options.verbose
go(args[0])
