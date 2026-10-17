/* Compte, confidentialité et préparation des pièces. */
const AccountTools=(()=>{
 const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n};
 const action=(text,fn,cls='ghost')=>{const b=node('button',text,cls);b.type='button';b.onclick=fn;return b};
 async function request(path,options={},timeout=30000){try{const r=await AccountSession.request(path,options,timeout),data=await r.json();if(!r.ok)throw Error(data.error||'Action indisponible. Réessayez.');return data}catch(e){throw Error(e.message==='timeout'?'Le délai est dépassé. Réessayez.':e.message==='Failed to fetch'?'Connexion indisponible. Réessayez.':e.message)}}
 async function prepareImported(photo){
  if(!photo)return {photo};
  const id=(photo||'').split('/').pop();
  const data=await request('/api/photo/'+id+'/prepare',{method:'POST'});
  const blob=await(await fetch(data.image)).blob();
  const url=await uploadCutout(blob);
  return {photo:url,sourcePhoto:photo,cutout:true,cutoutBounds:data.bounds,fitting:{}};
 }
 async function prepareExisting(item){
  if(!item.photo||item._preparing)return false;
  item._preparing=true;
  const before={photo:item.photo,sourcePhoto:item.sourcePhoto||'',cutout:item.cutout,cutoutBounds:item.cutoutBounds,fitting:item.fitting};let result;
  try{toast('Détourage en cours…');result=await prepareImported(item.sourcePhoto||item.photo);Object.assign(item,result);if(!await save())throw Error('Photo préparée, mais sauvegarde impossible. Réessayez.');GarmentPhotos.clear();renderItems();if(document.getElementById('outfits').classList.contains('active'))renderOutfits(false);if(before.photo!==result.sourcePhoto)fetch(before.photo,{method:'DELETE'}).catch(()=>{});toast('Fond retiré. Vérifiez les contours et retouchez si besoin.');return true}
  catch(e){Object.assign(item,before);if(result?.photo)fetch(result.photo,{method:'DELETE'}).catch(()=>{});toast(e.message);return false}finally{delete item._preparing}
 }
 async function prepareAll(button,status){
  const items=state.items.filter(p=>p.photo&&!p.cutout);button.disabled=true;
  let done=0,failed=0;
  for(const item of items){status.textContent=`Préparation ${done+failed+1} / ${items.length}`;if(await prepareExisting(item))done++;else failed++}
  status.textContent=items.length?`${done} photo(s) préparée(s). ${failed?failed+' à retoucher ou à réessayer.':'Vérifiez les contours.'}`:'Toutes vos photos sont déjà préparées.';button.disabled=false;
 }
 function confirmDelete(){
  const d=node('dialog',null,'ux-dialog security-dialog'),f=node('form'),title=node('h2','Supprimer mon compte ?'),description=node('p','Votre profil, vos vêtements, vos photos et vos sessions seront supprimés du service. Cette action est définitive.');
  const label=node('label','Votre mot de passe'),input=node('input');input.type='password';input.required=true;input.autocomplete='current-password';label.append(input);
  const status=node('p');status.setAttribute('role','alert');
  const cancel=action('Annuler',()=>d.close());const submit=node('button','Supprimer définitivement','danger');submit.type='submit';
  f.append(title,description,label,status,cancel,submit);d.append(f);document.body.append(d);d.onclose=()=>d.remove();d.showModal();input.focus();
  f.onsubmit=async event=>{event.preventDefault();submit.disabled=true;cancel.disabled=true;status.textContent='Suppression…';
   try{await request('/api/account/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:input.value})});loaded=false;state={user:null,items:[],settings:{},session:false};GarmentPhotos.clear();document.querySelector('.shell').style.visibility='hidden';location.replace('/login.html#deleted')}
   catch(e){status.textContent=e.message;submit.disabled=cancel.disabled=false}
  };
 }
 function silhouette(category){
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'),path=document.createElementNS(ns,'path');
  svg.setAttribute('viewBox','0 0 60 80');svg.setAttribute('aria-hidden','true');
  const paths={haut:'M18 12L9 19 3 34 13 39 15 65H45L47 39 57 34 51 19 42 12 36 19H24Z',bas:'M15 10H45L49 68H34L30 37 26 68H11Z',robe:'M21 9H39L42 28 55 68H5L18 28Z',veste:'M20 9L9 18 3 61 15 64 19 32 18 70H42L41 32 45 64 57 61 51 18 40 9 30 20Z',chaussures:'M8 42Q19 48 24 30L35 30 38 45Q43 50 55 51L55 62H8Z',accessoire:'M12 28H48L52 69H8ZM22 28V17Q30 6 38 17V28'};
  path.setAttribute('d',paths[category]||paths.haut);path.setAttribute('fill','#9baa8d');path.setAttribute('stroke','#6c805d');path.setAttribute('stroke-width','1.3');svg.append(path);return svg;
 }
 function init(){
  const card=node('section',null,'card security-card');card.append(node('h2','Mon compte et mes données'));
  const links=node('div',null,'security-links');for(const [text,url]of [['Confidentialité','/confidentialite.html'],['Assistance','/assistance.html'],['Découvrir l’application','/presentation.html']]){const a=node('a',text);a.href=url;links.append(a)}card.append(links);
  const recovery=node('a','Changer ou retrouver mon mot de passe');recovery.href='/login.html#forgot';card.append(recovery);
  const exp=node('a','Télécharger mes données et mes photos','ghost');exp.href='/api/account/export';exp.download='outfit-matcher-mes-donnees.zip';card.append(exp,action('Supprimer mon compte',confirmDelete,'danger'));
  document.querySelector('.settingcard').after(card);
  const photoTools=node('div',null,'photo-batch'),status=node('span');status.setAttribute('role','status');const b=action('Préparer mes photos',()=>prepareAll(b,status));photoTools.append(b,status);document.getElementById('filters').before(photoTools);
 }
 return {init,prepareImported,prepareExisting,request,silhouette};
})();
