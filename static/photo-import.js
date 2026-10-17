/* Photos Android/iPhone : essayer le décodage local, sinon envoyer le fichier brut. */
const PhotoImport=(()=>{
 const supported=file=>/\.(jpe?g|png|webp)$/i.test(file.name||'')||/^image\/(jpeg|png|webp)$/.test(file.type||'');
 async function snapshot(file){
  if(!file||file.size>25_000_000)throw Error('Choisissez une photo de moins de 25 Mo.');
  if(!supported(file))throw Error('Choisissez un fichier JPG, JPEG, PNG ou WebP.');
  // Copier les octets avant de vider le sélecteur : certains fournisseurs de fichiers
  // Android perdent l'accès au contenu dès que le formulaire est réinitialisé.
  let bytes;
  try{bytes=await file.arrayBuffer()}catch{throw Error('Impossible de lire la photo sélectionnée. Choisissez-la à nouveau dans la galerie.');}
  const ext=/\.([a-z0-9]+)$/i.exec(file.name||'')?.[1]?.toLowerCase();
  const type=/^image\/(jpeg|png|webp)$/.test(file.type||'')?file.type:({jpg:'image/jpeg',jpeg:'image/jpeg',png:'image/png',webp:'image/webp'}[ext]||'application/octet-stream');
  return new File([bytes],file.name||'photo.jpg',{type,lastModified:file.lastModified||Date.now()});
 }
 function fromImage(file){return new Promise((resolve,reject)=>{
  const url=URL.createObjectURL(file),img=new Image();
  img.onload=()=>{try{
   if(!img.naturalWidth||!img.naturalHeight)throw Error('Image vide');
   const scale=Math.min(1,850/Math.max(img.naturalWidth,img.naturalHeight)),canvas=document.createElement('canvas');
   canvas.width=Math.max(1,Math.round(img.naturalWidth*scale));canvas.height=Math.max(1,Math.round(img.naturalHeight*scale));
   canvas.getContext('2d').drawImage(img,0,0,canvas.width,canvas.height);
   resolve(canvas.toDataURL(file.type==='image/png'?'image/png':'image/jpeg',.82));
  }catch(err){reject(err)}finally{URL.revokeObjectURL(url)}};
  img.onerror=()=>{URL.revokeObjectURL(url);reject(Error('Décodage du navigateur impossible.'))};img.src=url;
 })}
 async function fromBitmap(file){
  if(typeof createImageBitmap!=='function')throw Error('Décodeur indisponible');
  const bitmap=await createImageBitmap(file);
  try{const scale=Math.min(1,850/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);return canvas.toDataURL('image/jpeg',.82)}finally{bitmap.close()}
 }
 async function compress(file){
  if(!file||file.size>25_000_000)throw Error('Choisissez une photo de moins de 25 Mo.');
  if(!supported(file))throw Error('Choisissez un fichier JPG, JPEG, PNG ou WebP.');
  try{return await fromImage(file)}catch{}
  try{return await fromBitmap(file)}catch{}
  // Le serveur contrôle le contenu et normalise l'image après l'envoi unique.
  return file;
 }
 return {compress,snapshot};
})();
