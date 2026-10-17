/* Parcours dressing : les photos restent celles du compte. */
const Experience = (() => {
 const locks=new Set(); let favoritesOnly=false,queue=[],previewURL='',importPending=Promise.resolve(true),importToken=0;
 const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n};
 const button=(text,fn)=>{const n=el('button',text,'ghost');n.type='button';n.onclick=fn;return n};
 function select(label,values,value,change){const wrap=el('label',label,'ux-field'),input=el('select');for(const [v,t]of values){const o=el('option',t);o.value=v;input.append(o)}input.value=value;input.onchange=()=>change(input.value);wrap.append(input);return wrap}
 function preferences(){return state.settings.preferences||{}}
 function init(){
  const status=el('span','Compte chargé','ux-status');status.id='saveStatus';status.setAttribute('role','status');document.querySelector('.top .right').prepend(status);
  const panel=el('div',null,'card ux-preferences');panel.id='uxPreferences';document.querySelector('.settingcard').before(panel);
  const tools=el('div',null,'ux-tools');tools.id='uxTools';document.getElementById('outfitWeather').after(tools);
  const photo=document.getElementById('itemPhoto');
  const progress=el('div',null,'ux-upload');progress.id='uxUpload';photo.after(progress);
  const addFiles=picker=>{
   const files=Array.from(picker.files||[]);if(!files.length)return;
   const token=++importToken,area=document.getElementById('uxUpload');
   queue=[];area.textContent='Préparation des photos…';
   importPending=(async()=>{
    if(files.length>6)throw Error('Choisissez jusqu’à 6 photos à la fois.');
    const copies=[];
    for(const file of files)copies.push(await PhotoImport.snapshot(file));
    if(token===importToken){queue=copies;picker.value='';showQueue()}
    return true;
   })().catch(err=>{if(token===importToken){queue=[];area.textContent='';toast(err.message||'Impossible de lire cette photo.')}return false});
  };
  photo.multiple=true;photo.onchange=()=>addFiles(photo);
  const camera=document.getElementById('itemCamera'),openCamera=document.getElementById('openCamera');
  camera.onchange=()=>addFiles(camera);
  if(/Android/i.test(navigator.userAgent)){openCamera.hidden=false;openCamera.onclick=()=>camera.click()}
  const campaign=document.querySelector('.campaign');document.getElementById('home').append(campaign);
 }
 function showQueue(){const area=document.getElementById('uxUpload');area.replaceChildren();if(previewURL){URL.revokeObjectURL(previewURL);previewURL=''}if(!queue.length)return;
  previewURL=URL.createObjectURL(queue[0]);const img=el('img');img.src=previewURL;img.alt='Photo à renseigner';img.onerror=()=>{img.hidden=true;img.after(el('p','Aperçu indisponible · '+queue[0].name+'. La photo peut tout de même être ajoutée.','muted'))};area.append(img,el('p',`${queue.length} photo(s) restante(s). Choisissez le type, la couleur et le motif de cette pièce.`),button('Passer cette photo',()=>{queue.shift();showQueue()}));
 }
 function nextPhoto(){queue.shift();showQueue()}
 function refresh(){
  const panel=document.getElementById('uxPreferences');if(!panel)return;panel.replaceChildren(el('h3','Mes préférences'));
  const prefs=preferences();
  const options={fit:['Coupe préférée',[['any','Toutes les coupes'],['ample','Ample'],['ajuste','Ajustée']]],shoes:['Chaussures',[['any','Toutes'],['flat','Sans talons']]],length:['Longueur',[['any','Toutes'],['long','Éviter les pièces courtes']]],cold:['Sensibilité au froid',[['normal','Normale'],['sensitive','J’ai facilement froid'],['warm','J’ai souvent chaud']]]};
  for(const [key,[label,values]]of Object.entries(options))panel.append(select(label,values,prefs[key]||values[0][0],async value=>{const old=state.settings.preferences;state.settings.preferences={...preferences(),[key]:value};if(!await save()){state.settings.preferences=old;refresh()}}));
 }
 function compose(item){locks.clear();locks.add(item.id);favoritesOnly=false;showPage('outfits')}
 function wardrobe(item,container){
  container.append(select('Disponibilité',[['available','Disponible'],['laundry','Au lavage'],['stored','Rangé pour la saison']],item.availability||'available',async v=>{const old=item.availability;item.availability=v;if(!await save()){item.availability=old;renderItems()}}));
  const b=button('Composer avec cette pièce',()=>compose(item));container.append(b);
 }
 function tools(){const area=document.getElementById('uxTools');area.replaceChildren();
  area.append(select('Ambiance',[['auto','Selon l’occasion'],['casual','Décontractée · café, balade'],['smart','Soignée · bureau, restaurant']],state.settings.formality||'auto',async v=>{const old=state.settings.formality;state.settings.formality=v;if(await save())renderOutfits(false);else state.settings.formality=old}));
  const fav=button(favoritesOnly?'Voir toutes les propositions':'Mes favoris',()=>{favoritesOnly=!favoritesOnly;renderOutfits(false)});fav.setAttribute('aria-pressed',String(favoritesOnly));area.append(fav);
  for(const id of locks){const item=state.items.find(x=>x.id===id);if(!item){locks.delete(id);continue}area.append(button('✓ '+item.name+' ×',()=>{locks.delete(id);renderOutfits(false)}))}
 }
 function filter(ranked){return ranked.filter(l=>[...locks].every(id=>l.pieces.some(p=>p.id===id))&&(!favoritesOnly||(state.settings.favorites||[]).includes(l.key)))}
 function zoom(piece){const d=el('dialog',null,'ux-dialog'),img=el('img');img.src=piece.photo;img.alt=piece.name;d.append(button('Fermer',()=>d.close()),el('h3',piece.name),img);document.body.append(d);d.onclose=()=>d.remove();d.showModal()}
 function openPieceOptions(piece,trigger,actions){
  document.getElementById('pieceOptionsDialog')?.close();
  const dialog=el('dialog',null,'ux-dialog piece-options-dialog');dialog.id='pieceOptionsDialog';
  dialog.setAttribute('aria-label','Options pour '+piece.name);
  const head=el('div',null,'piece-options-head');head.append(el('h2',piece.name));
  const close=button('✕',()=>dialog.close());close.className='piece-options-close';close.setAttribute('aria-label','Fermer les options');head.append(close);
  const content=el('div',null,'piece-menu');
  for(const [label,action,pressed]of actions){const b=button(label,()=>{dialog.close();action()});if(pressed!==undefined)b.setAttribute('aria-pressed',String(pressed));content.append(b)}
  dialog.append(head,content);document.body.append(dialog);trigger.setAttribute('aria-expanded','true');
  dialog.onclose=()=>{dialog.remove();trigger.setAttribute('aria-expanded','false');if(trigger.isConnected&&!document.querySelector('dialog[open]'))trigger.focus()};
  dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close()});
  dialog.showModal();content.querySelector('button')?.focus();
 }
 function tile(piece,tile,look,ranked,card){
  const image=tile.querySelector('img');if(image){prepareImage(piece,image,tile);const b=button('Voir '+piece.name,()=>zoom(piece));b.className='ux-photo';image.replaceWith(b);b.replaceChildren(image);b.setAttribute('aria-label','Agrandir '+piece.name)}
  function replace(){
   const other=look.pieces.filter(x=>x.id!==piece.id).map(x=>x.id);
   const candidates=ranked.filter(l=>!l.pieces.some(x=>x.id===piece.id)&&other.every(id=>l.pieces.some(x=>x.id===id))&&l.pieces.length===look.pieces.length&&[...locks].every(id=>l.pieces.some(x=>x.id===id)));
   if(!candidates.length){toast(locks.has(piece.id)?'Libérez cette pièce avant de la remplacer.':'Aucune autre pièce compatible. Ajoutez-en ou changez l’occasion.');return}
   const d=el('dialog',null,'ux-dialog');d.append(el('h3','Choisir une autre pièce'),button('Fermer',()=>d.close()));const seen=new Set();
   for(const candidate of candidates){const replacement=candidate.pieces.find(x=>!other.includes(x.id));if(!replacement||seen.has(replacement.id))continue;seen.add(replacement.id);const b=button(replacement.name,()=>{d.close();renderLook(candidate,Number(card.dataset.index),ranked,card)});if(replacement.photo){const im=el('img');im.src=replacement.photo;im.alt='';im.style.height='100px';b.prepend(im)}d.append(b)}document.body.append(d);d.onclose=()=>d.remove();d.showModal();
  }
  const menu=button('•••',()=>{
   const actions=[
    [locks.has(piece.id)?'✓ Gardée · libérer':'Garder cette pièce',()=>{locks.has(piece.id)?locks.delete(piece.id):locks.add(piece.id);renderOutfits(false)},locks.has(piece.id)],
    ['Remplacer',replace]
   ];
   if(piece.photo&&!state.user?.demo)actions.push(['Détourer automatiquement',()=>AccountTools.prepareExisting(piece)],['Retoucher le fond',()=>window.openCutout?.(piece)],['Voir la photo originale',()=>zoom({...piece,photo:piece.sourcePhoto||piece.photo})]);
   openPieceOptions(piece,menu,actions);
  });
  menu.className='piece-options';menu.setAttribute('aria-label','Options pour '+piece.name);menu.setAttribute('aria-haspopup','dialog');menu.setAttribute('aria-expanded','false');tile.append(menu);
 }
 async function prepareImage(piece,image,tile){
  if(!window.GarmentPhotos||!piece.photo)return;
  try{const result=await GarmentPhotos.load(piece);if(!image.isConnected)return;
   if(result.ready){image.src=result.url;tile.dataset.prepared='true'}
   else{const hint=el('span','Fond à retoucher','photo-status');tile.append(hint)}
  }catch{if(image.isConnected){const hint=el('span','Préparation indisponible','photo-status');tile.append(hint)}}
 }

 function actions(look,area){
  const saved=(state.settings.favorites||[]).includes(look.key),fav=button(saved?'♥ Enregistrée':'♡ Enregistrer',async()=>{const old=state.settings.favorites||[];state.settings.favorites=saved?old.filter(k=>k!==look.key):[...old,look.key].slice(-100);if(!await save())state.settings.favorites=old;renderOutfits(false)});fav.setAttribute('aria-pressed',String(saved));area.prepend(fav);
  area.prepend(button('Je porte cette tenue',async()=>{const old=state.settings.worn||[];state.settings.worn=[...old.filter(x=>x.key!==look.key),{key:look.key,ids:look.pieces.map(p=>p.id),at:Date.now()}].slice(-100);if(await save()){toast('Tenue portée enregistrée. Les prochaines propositions varieront.');renderOutfits(false)}else state.settings.worn=old}));
 }
 function reason(submit){const d=el('dialog',null,'ux-dialog');d.append(el('h3','Qu’est-ce qui ne vous plaît pas ?'),el('p','Facultatif : ce retour est conservé avec votre vote.'),button('Fermer',()=>d.close()));for(const text of ['Couleurs','Coupe','Trop habillé','Trop décontracté','Météo','Sans précision'])d.append(button(text,()=>{d.close();submit(text)}));document.body.append(d);d.onclose=()=>d.remove();d.showModal()}
 function empty(){return favoritesOnly?'Aucun favori compatible avec ces filtres. Essayez « Toutes les propositions ».':locks.size?'Aucune tenue avec ces pièces. Libérez une pièce ou vérifiez ses occasions et sa disponibilité.':''}
 return {init,refresh,prepareImage,reason,openPieceOptions,compose,wardrobe,tools,filter,tile,actions,empty,photo:()=>queue[0]||null,readyPhoto:()=>importPending,nextPhoto};
})();
