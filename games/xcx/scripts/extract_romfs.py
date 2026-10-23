#!/usr/bin/env python3
"""index_romfs: the file table of a local RomFS image (merge_romfs.py, extract_shared_fonts.py, extract_app_icon.py).

Adapted from the original MacNativeGames TotK scripts; its TotK-only command line (extracting one base RomFS) was
removed: this port's merged RomFS comes from merge_romfs.py.
"""
import struct
from extract_exefs import read_at, require


def index_romfs(path):
    with path.open('rb') as f:
        h=struct.unpack('<10Q',read_at(f,0,80));require(h[0]==80,'RomFS header');length=path.stat().st_size
        for off,size in [(h[1],h[2]),(h[3],h[4]),(h[5],h[6]),(h[7],h[8])]:require(off+size<=length,'RomFS table bounds')
        dirs=read_at(f,h[3],h[4]);entries=read_at(f,h[7],h[8]);files=[];seen_dirs=set();seen_files=set();paths=set()
        def name(table,off,n):
            require(off+n<=len(table),'Name bounds');s=table[off:off+n].decode('utf-8');require(s not in ['.','..'] and '/' not in s and '\\' not in s and '\0' not in s,'Unsafe path');return s
        todo=[(0,'')]
        while todo:
            off,parent=todo.pop();require(off not in seen_dirs and off+24<=len(dirs),'Invalid/cyclic directory');seen_dirs.add(off)
            p,sibling,child,file,hash_next,n=struct.unpack_from('<6I',dirs,off);part=name(dirs,off+24,n);prefix=parent+part+'/' if part else parent
            if sibling!=0xffffffff:todo.append((sibling,parent))
            if child!=0xffffffff:todo.append((child,prefix))
            while file!=0xffffffff:
                require(file not in seen_files and file+32<=len(entries),'Invalid/cyclic file');seen_files.add(file)
                p,next_file,data,size,hash_next,n=struct.unpack_from('<IIQQII',entries,file);part=name(entries,file+32,n);require(part!='','Empty file name');filename=prefix+part
                require(filename not in paths and h[9]+data+size<=length,'Duplicate/out-of-bounds file');paths.add(filename)
                files.append(dict(path=filename,offset=h[9]+data,size=size));file=next_file
        return sorted(files,key=lambda item:item['path'])
