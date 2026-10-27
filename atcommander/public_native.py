"""Bounded offline inspection of official public WebGL code. Never execute it."""
from __future__ import annotations
import bisect
import hashlib
import json
import struct

class NativeCompatibilityError(ValueError):pass

def _leb(data,at,signed=False):
    value=shift=0
    for _ in range(10):
        if at>=len(data):raise NativeCompatibilityError("Truncated native integer")
        byte=data[at];at+=1;value|=(byte&127)<<shift;shift+=7
        if not byte&128:
            if signed and byte&64:value-=1<<shift
            return value,at
    raise NativeCompatibilityError("Native integer exceeded its bound")

def _sleb(value):
    out=bytearray()
    for _ in range(10):
        byte=value&127;value>>=7
        done=(value==0 and not byte&64) or (value==-1 and byte&64)
        out.append(byte if done else byte|128)
        if done:return bytes(out)
    raise NativeCompatibilityError("Native integer exceeded its bound")

def _expression(data,at):
    if at>=len(data) or data[at]!=65:raise NativeCompatibilityError("Unsupported native offset")
    value,at=_leb(data,at+1,True)
    if at>=len(data) or data[at]!=11:raise NativeCompatibilityError("Unsupported native offset")
    return value,at+1

def unity_archive(data):
    """Read named files from a public UnityWebData archive, with strict bounds."""
    if not data.startswith(b"UnityWebData1.0\0") or len(data)<20:
        raise NativeCompatibilityError("Unrecognized public client archive")
    end=struct.unpack_from("<I",data,16)[0];at=20;files={}
    if not 20<=end<=min(len(data),1024*1024):raise NativeCompatibilityError("Invalid client archive header")
    while at<end:
        if at+12>end or len(files)>=1024:raise NativeCompatibilityError("Invalid client archive entries")
        start,size,length=struct.unpack_from("<III",data,at);at+=12
        if not 1<=length<=1024 or at+length>end or start<end or start+size>len(data):
            raise NativeCompatibilityError("Invalid client archive boundaries")
        name=data[at:at+length].decode("utf-8");at+=length
        if name in files:raise NativeCompatibilityError("Duplicate client archive entry")
        files[name]=memoryview(data)[start:start+size]
    if at!=end:raise NativeCompatibilityError("Invalid client archive header")
    return files

class NativeBuild:
    def __init__(self,wasm,metadata,should_stop=lambda:False):
        self.stopped=should_stop
        if not wasm.startswith(b"\0asm\x01\0\0\0") or len(wasm)>160*1024*1024:
            raise NativeCompatibilityError("Unrecognized public native code")
        if not 380<=len(metadata)<=32*1024*1024:raise NativeCompatibilityError("Invalid public metadata size")
        self.metadata=metadata;self.h=struct.unpack_from("<95I",metadata)
        if self.h[:3]!=(0xFAB11BAF,39,380):raise NativeCompatibilityError("Unsupported public metadata layout")
        for at in range(2,95,3):
            if self.h[at]+self.h[at+1]>len(metadata):raise NativeCompatibilityError("Invalid public metadata boundaries")
        self.sections={};at=8
        while at<len(wasm):
            kind=wasm[at];size,start=_leb(wasm,at+1);end=start+size
            if end>len(wasm) or kind in self.sections and kind!=0:raise NativeCompatibilityError("Invalid native sections")
            if kind:self.sections[kind]=memoryview(wasm)[start:end]
            at=end
        self._memory();self._table();self._bodies();self._metadata()

    def check(self):
        if self.stopped():raise NativeCompatibilityError("Client update cancelled")

    def u32(self,at):
        if not 0<=at<=len(self.memory)-4:raise NativeCompatibilityError("Invalid native pointer")
        return struct.unpack_from("<I",self.memory,at)[0]

    def locations(self,value):
        needle=struct.pack("<I",value);at=self.memory.find(needle)
        while at>=0:
            if at%4==0:yield at
            at=self.memory.find(needle,at+1)

    def _memory(self):
        data=self.sections[11];count,at=_leb(data,0);segments=[]
        if count>100000:raise NativeCompatibilityError("Too many native data segments")
        for _ in range(count):
            flags,at=_leb(data,at)
            if flags not in (0,1,2):raise NativeCompatibilityError("Unsupported native data segment")
            if flags==2:
                target,at=_leb(data,at)
                if target!=0:raise NativeCompatibilityError("Unsupported native memory")
            if flags!=1:offset,at=_expression(data,at)
            size,at=_leb(data,at)
            if at+size>len(data):raise NativeCompatibilityError("Truncated native data")
            if flags!=1:
                if offset<0 or offset+size>64*1024*1024:raise NativeCompatibilityError("Native memory limit exceeded")
                segments.append((offset,data[at:at+size]))
            at+=size
        if not segments or at!=len(data):raise NativeCompatibilityError("Invalid native data")
        self.memory=bytearray(max(start+len(block) for start,block in segments))
        for start,block in segments:self.memory[start:start+len(block)]=block

    def _table(self):
        data=self.sections[9];count,at=_leb(data,0);self.table={}
        if count>10000:raise NativeCompatibilityError("Native table limit exceeded")
        for _ in range(count):
            flag,at=_leb(data,at)
            if flag not in (0,2):raise NativeCompatibilityError("Unsupported native table layout")
            if flag==2:
                target,at=_leb(data,at)
                if target!=0:raise NativeCompatibilityError("Unsupported native table")
            start,at=_expression(data,at)
            if flag==2:
                if data[at]!=0:raise NativeCompatibilityError("Unsupported native table type")
                at+=1
            length,at=_leb(data,at)
            if start<0 or length>500000 or start+length>500000:raise NativeCompatibilityError("Native table limit exceeded")
            for i in range(length):
                function,at=_leb(data,at);self.table[start+i]=function
        if at!=len(data):raise NativeCompatibilityError("Invalid native table")

    def _bodies(self):
        data=self.sections[2];count,at=_leb(data,0);imports=0
        if count>100000:raise NativeCompatibilityError("Native import limit exceeded")
        for _ in range(count):
            for _ in range(2):
                size,at=_leb(data,at);at+=size
                if at>=len(data):raise NativeCompatibilityError("Truncated native import")
            kind=data[at];at+=1
            if kind==0:_,at=_leb(data,at);imports+=1
            elif kind in (1,2):
                if kind==1:at+=1
                flags,at=_leb(data,at);_,at=_leb(data,at)
                if flags&1:_,at=_leb(data,at)
            elif kind==3:at+=2
            else:raise NativeCompatibilityError("Unsupported native import")
        data=self.sections[10];count,at=_leb(data,0);self.bodies={}
        if count>500000:raise NativeCompatibilityError("Native method limit exceeded")
        for index in range(count):
            size,at=_leb(data,at)
            if at+size>len(data):raise NativeCompatibilityError("Truncated native method")
            self.bodies[imports+index]=data[at:at+size];at+=size
        if at!=len(data):raise NativeCompatibilityError("Invalid native method table")

    def text(self,index):
        at=self.h[8]+index;end=self.metadata.find(b"\0",at,self.h[8]+self.h[9])
        if not self.h[8]<=at<=end<self.h[8]+self.h[9]:raise NativeCompatibilityError("Invalid public metadata string")
        return self.metadata[at:end].decode("utf-8")

    def _metadata(self):
        h=self.h
        if h[61]>100000 or h[19]>500000:raise NativeCompatibilityError("Public metadata limit exceeded")
        self.owners=[""]*h[19];self.types={}
        for index in range(h[61]):
            at=h[59]+index*76
            name_index,namespace_index=struct.unpack_from("<II",self.metadata,at)
            name=self.text(name_index);namespace=self.text(namespace_index) if namespace_index else ""
            owner=(namespace+"." if namespace else "")+name;self.types[index]=owner
            first=struct.unpack_from("<I",self.metadata,at+24)[0]
            count=struct.unpack_from("<H",self.metadata,at+52)[0]
            if count and first+count>h[19]:raise NativeCompatibilityError("Invalid public method ownership")
            for method in range(first,first+count):self.owners[method]=owner
        modules=[]
        start=self.memory.find(b"Assembly-CSharp.dll\0")
        while start>=0:
            for at in self.locations(start):
                if at+12>len(self.memory):continue
                count,array=self.u32(at+4),self.u32(at+8)
                if 0<count<50000 and 0<array<len(self.memory)-count*4:modules.append((count,array))
            start=self.memory.find(b"Assembly-CSharp.dll\0",start+1)
        if len(modules)!=1:raise NativeCompatibilityError("Public code module is ambiguous")
        count,array=modules[0];self.module_count=count;self.module_array=array;self.names={};self.hash_function=None
        for index,owner in enumerate(self.owners):
            if owner!="ServerMessageHandler":continue
            name=self.method_name(index)
            token=struct.unpack_from("<I",self.metadata,h[17]+index*30+18)[0]
            slot=(token&0xffffff)-1
            if token>>24!=6 or not 0<=slot<count:raise NativeCompatibilityError("Invalid public signer token")
            function=self.table.get(self.u32(array+slot*4))
            if name=="GenerateHash":
                if self.hash_function is not None:raise NativeCompatibilityError("Public hash helper is ambiguous")
                self.hash_function=function
        if self.hash_function not in self.bodies:raise NativeCompatibilityError("Public hash helper is unavailable")
        # Match the reference parser's public C# direct-call names.
        for local in range(min(count,18500)):
            function=self.table.get(self.u32(array+local*4))
            if function is not None and local<h[19]:self.names[function]=self.method_name(local)
        candidates=[]
        for at in self.locations(h[61]):
            base=at-40
            if (base>=0 and base+64<len(self.memory) and self.u32(base+48)==h[61]
                    and 0<self.u32(base+44)<len(self.memory)-h[61]*4
                    and 0<self.u32(base+52)<len(self.memory)-h[61]*4
                    and 27048<=self.u32(base+24)<65536
                    and 0<self.u32(base+28)<len(self.memory)-self.u32(base+24)*4):
                candidates.append(base)
        if len(candidates)!=1:raise NativeCompatibilityError("Public type registration is ambiguous")
        self.registration=candidates[0]

    def method_name(self,index):
        return self.text(struct.unpack_from("<I",self.metadata,self.h[17]+index*30)[0])

    def method_function(self,owner,name):
        matches=[index for index,value in enumerate(self.owners) if value==owner and self.method_name(index)==name]
        if len(matches)!=1:raise NativeCompatibilityError("Public profile helper is ambiguous")
        token=struct.unpack_from("<I",self.metadata,self.h[17]+matches[0]*30+18)[0]
        slot=(token&0xffffff)-1
        if token>>24!=6 or not 0<=slot<self.module_count:raise NativeCompatibilityError("Invalid public profile helper")
        function=self.table.get(self.u32(self.module_array+slot*4))
        if function not in self.bodies:raise NativeCompatibilityError("Public profile helper is unavailable")
        return function

    def literal_labels(self,message_format,client_format):
        h=self.h;labels={};formats={};literal_cache={};method_cache={};type_cache={}
        type_count=self.u32(self.registration+24);type_array=self.u32(self.registration+28)
        for offset,(word,) in enumerate(struct.iter_unpack("<I",self.memory[:len(self.memory)//4*4])):
            if offset%65536==0:self.check()
            if not word&1:continue
            kind,index=word>>29,(word&0x1ffffffe)>>1
            label=None
            if kind==5 and index+1<h[4]:
                if index not in literal_cache:
                    start,end=struct.unpack_from("<II",self.metadata,h[2]+index*4)
                    if not 0<=start<=end<=h[6]:raise NativeCompatibilityError("Invalid public literal")
                    raw=self.metadata[h[5]+start:h[5]+end]
                    literal_cache[index]=("literal",hashlib.sha256(raw).hexdigest())
                    if raw==message_format:formats[offset*4]="message-format"
                    elif raw==client_format:formats[offset*4]="client-format"
                else:
                    start,end=struct.unpack_from("<II",self.metadata,h[2]+index*4)
                    raw=self.metadata[h[5]+start:h[5]+end]
                    if raw==message_format:formats[offset*4]="message-format"
                    elif raw==client_format:formats[offset*4]="client-format"
                label=literal_cache[index]
            elif kind==3 and index<h[19]:
                if index not in method_cache:method_cache[index]=("method",self.owners[index],self.method_name(index))
                label=method_cache[index]
            elif kind in (1,2) and index<type_count:
                if index not in type_cache:
                    pointer=self.u32(type_array+index*4);tag=(self.u32(pointer+4)>>16)&255
                    target=self.u32(pointer)
                    type_cache[index]=("type",kind,self.types[target]) if tag in (17,18) and target in self.types else None
                label=type_cache[index]
            if label is not None:labels[offset*4]=label
        if set(formats.values())!={"message-format","client-format"}:
            raise NativeCompatibilityError("The public signing formats changed")
        labels.update(formats)
        return labels,formats

    def instructions(self,function):
        body=self.bodies[function];count,at=_leb(body,0)
        for _ in range(count):_,at=_leb(body,at);at+=1
        out=[]
        while at<len(body):
            op=body[at];at+=1;args=[]
            if op in (2,3,4):_,at=_leb(body,at,True)
            elif op in (12,13,16,32,33,34,35,36,37,38,63,64,210):
                value,at=_leb(body,at);args=[value]
                if op==16:args.append(self.names.get(value,""))
            elif op==17:
                first,at=_leb(body,at);second,at=_leb(body,at);args=[first,second]
            elif op==14:
                n,at=_leb(body,at)
                for _ in range(n+1):value,at=_leb(body,at);args.append(value)
            elif 40<=op<=62:
                first,at=_leb(body,at);second,at=_leb(body,at);args=[first,second]
            elif op in (65,66):value,at=_leb(body,at,True);args=[value]
            elif op in (67,68):
                size=4 if op==67 else 8;args=[bytes(body[at:at+size]).hex()];at+=size
            elif op==208:args=[body[at]];at+=1
            elif op==252:
                sub,at=_leb(body,at);args=[sub]
                n=2 if sub in (8,10,12,14) else 1 if sub in (9,11,13,15,16,17) else 0
                for _ in range(n):value,at=_leb(body,at);args.append(value)
            elif op==28:
                n,at=_leb(body,at);args=list(body[at:at+n]);at+=n
            elif not (op in (0,1,5,11,15,26,27,209) or 69<=op<=196):
                raise NativeCompatibilityError("Unsupported public signer instruction")
            if at>len(body) or len(out)>20000:raise NativeCompatibilityError("Public signer method limit")
            out.append((op,args))
        return out

    def fingerprint(self,function,labels):
        maps={};out=[]
        def symbol(kind,value):
            values=maps.setdefault(kind,{})
            if value not in values:values[value]=len(values)
            return kind,values[value]
        for op,args in self.instructions(function):
            if op==65:
                value=args[0]
                if value in labels:args=[labels[value]]
                elif 512<=value<65536 and value not in {1000,1024,2000,2048,3600,4096,5000,10000,30000,60000} and value in self.table:
                    target=self.table[value]
                    args=["GenerateHash" if target==self.hash_function else symbol("table-call",target)]
                elif value>=65536:args=[symbol("address",value)]
            elif op==16:args=[args[1] if args[1] else symbol("call",args[0])]
            elif op==17:args=[symbol("signature",args[0]),args[1]]
            out.append([op,*args])
        return hashlib.sha256(json.dumps(out,separators=(",",":")).encode()).hexdigest(),len(out)

    def signer_fingerprints(self,message_format,client_format):
        labels,formats=self.literal_labels(message_format,client_format)
        groups=[]
        for name in ("message-format","client-format"):
            needles=[b"\x41"+_sleb(address) for address,label in formats.items() if label==name]
            groups.append({index for index,body in self.bodies.items() if len(body)<100000
                           and any(bytes(body).find(needle)>=0 for needle in needles)})
        candidates=groups[0]&groups[1]
        if not 1<=len(candidates)<=8:raise NativeCompatibilityError("Public signer is ambiguous")
        return {"hash":self.fingerprint(self.hash_function,labels),
                "version":self.fingerprint(self.method_function("TyrantMobileService","GetVersionNumber"),labels),
                "unity":self.fingerprint(self.method_function("ServerMessageHandler","get_UnityVersion"),labels),
                "send":[self.fingerprint(function,labels) for function in sorted(candidates)]}
