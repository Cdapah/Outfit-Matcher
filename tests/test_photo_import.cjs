const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class ImageThatFails{
 set src(_) {queueMicrotask(()=>this.onerror())}
}
const context={
 URL:{createObjectURL:()=> 'blob:photo',revokeObjectURL:()=>{}},
 Image:ImageThatFails,createImageBitmap:async()=>{throw Error('unsupported')},
 queueMicrotask,
 File:class {constructor(parts,name,opts){this.bytes=parts[0];this.name=name;this.type=opts.type;this.lastModified=opts.lastModified;this.size=this.bytes.byteLength}}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/photo-import.js','utf8')+'\nglobalThis.importer=PhotoImport;',context);
(async()=>{
 const original={name:'camera.JPEG',type:'application/octet-stream',size:6_000_000};
 const data=await context.importer.compress(original);
 assert.equal(data,original,'undecodable JPEG must be uploaded directly, without a second normalization request');
 const bytes=new Uint8Array([255,216,255,224]).buffer;
 let permission=true;
 const samsung={name:'galerie.JPEG',type:'application/octet-stream',size:bytes.byteLength,lastModified:123,arrayBuffer:async()=>{if(!permission)throw Error('content URI revoked');return bytes}};
 const copy=await context.importer.snapshot(samsung);
 permission=false;
 assert.deepEqual([...new Uint8Array(copy.bytes)],[255,216,255,224],'copy retains the JPEG after original file access is lost');
 assert.equal(copy.type,'image/jpeg');
 assert.equal(await context.importer.compress(copy),copy,'the fallback can upload the independent JPEG copy');
 await assert.rejects(context.importer.compress({name:'fake.pdf',type:'application/pdf',size:200}),/JPG/);
 await assert.rejects(context.importer.compress({name:'huge.jpeg',type:'image/jpeg',size:26_000_000}),/25 Mo/);
 console.log('OK: Android JPEG/octet-stream is sent once to the photo endpoint; unsupported and oversized files rejected.');
})().catch(e=>{console.error(e);process.exitCode=1});
