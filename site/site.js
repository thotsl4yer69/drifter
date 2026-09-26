/* DRIFTER site interactions. Local only: no account, tracking or form endpoint. */
(()=>{'use strict';
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],email='mazlabz.ai@gmail.com';
const announce=(id,text)=>{const node=$(id);if(node){node.textContent=text;node.classList.add('show');}};
function download(name,data,type='text/plain;charset=utf-8'){
 const blob=data instanceof Blob?data:new Blob([data],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');
 a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
}
const menu=$('.menu-toggle'),nav=$('#navigation');
function setMenu(open){if(!menu||!nav)return;nav.classList.toggle('open',open);menu.setAttribute('aria-expanded',String(open));menu.textContent=open?'CLOSE −':'MENU +';document.body.classList.toggle('menu-open',open);}
menu?.addEventListener('click',()=>setMenu(menu.getAttribute('aria-expanded')!=='true'));
$$('.nav a').forEach(a=>a.addEventListener('click',()=>setMenu(false)));
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&menu?.getAttribute('aria-expanded')==='true'){setMenu(false);menu.focus();}});
matchMedia('(min-width: 901px)').addEventListener('change',e=>{if(e.matches)setMenu(false);});

// A form drafts a message. Only the user's email application can send it.
const form=$('#apply-form');
if(form){
 const params=new URLSearchParams(location.search),model=params.get('adapter'),hardware=params.get('hardware');
 if(model)$('#adapter').value=model.slice(0,120);
 if(hardware&&[...$('#hardware').options].some(o=>o.value===hardware))$('#hardware').value=hardware;
 const validate=()=>{for(const id of ['vehicle','adapter','goal']){const n=$('#'+id);n.setCustomValidity(n.value.trim()?'':'Please enter a value, not only spaces.');}return form.reportValidity();};
 const text=()=>['DRIFTER VIM — Field application','','Vehicle: '+$('#vehicle').value.trim(),'OBD adapter: '+$('#adapter').value.trim(),'Computer: '+$('#hardware').value,'','What I would like to investigate:',$('#goal').value.trim(),'','This is a prototype field-testing application, not an order.'].join('\n');
 form.addEventListener('input',e=>{if(typeof e.target.setCustomValidity==='function')e.target.setCustomValidity('');$('#form-message')?.classList.remove('show');$('#copy-area')?.classList.remove('show');});
 form.addEventListener('submit',e=>{e.preventDefault();if(!validate())return;announce('#form-message','Email prepared, not sent. Review it in your email app and press Send there. No email app? Copy or save the application and email '+email+'.');location.href='mailto:'+email+'?subject='+encodeURIComponent('DRIFTER VIM — Field application')+'&body='+encodeURIComponent(text());});
 $('#copy-application')?.addEventListener('click',async()=>{if(!validate())return;try{if(!navigator.clipboard?.writeText)throw Error('Clipboard not available');await navigator.clipboard.writeText(text());announce('#form-message','Copied. Send the application to '+email+'. Nothing has been submitted from this page.');}catch{const area=$('#copy-area');area.value=text();area.classList.add('show');area.focus();area.select();announce('#form-message','Copy the text below and send it to '+email+'.');}});
 $('#save-application')?.addEventListener('click',()=>{if(!validate())return;download('DRIFTER-field-application.txt',text());announce('#form-message','Application saved as a text file. Email it to '+email+'; it has not been sent.');});
}

const linkStates={adapter:['01 / Transport','The adapter answers.','The ELM handshake proves adapter communication. It does not prove that the vehicle ECU has answered.'],ecu:['02 / ECU','The vehicle responds.','A standard OBD response proves ECU communication. The set of usable measurements still depends on supported PIDs.'],pid:['03 / Data','Supported values arrive.','A live PID stream is evidence of actual data flow. Unavailable values stay unknown; a reachable adapter alone is not enough.']};
$$('[data-link-state]').forEach(button=>button.addEventListener('click',()=>{const state=linkStates[button.dataset.linkState];if(!state)return;$$('[data-link-state]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));['#link-kicker','#link-heading','#link-copy'].forEach((id,i)=>$(id).textContent=state[i]);}));
const transportNotes={
 bluetooth:['Bluetooth reader','Record the exact Bluetooth reader model, then use the touchscreen pairing and ELM-handshake flow before testing the ECU.','ELM327 / Bluetooth'],
 wifi:['Wi-Fi reader','Record the reader model and its documented network/endpoint. Confirm the ELM handshake first, then ECU response. A single Wi-Fi radio may change the node’s network connection.','ELM327 / Wi-Fi'],
 serial:['USB or serial reader','Record the exact adapter and serial connection. Prove the ELM handshake before treating an open serial port as a vehicle link.','ELM327 / USB or serial'],
 socketcan:['SocketCAN path','Confirm that the vehicle and interface actually support the intended CAN path. An OBD connector alone does not establish the protocol.','SocketCAN-class interface'],
 unknown:['Identify the connection first.','Record the vehicle and any markings on the reader. Start an enquiry before treating an unknown adapter as supported.','Not identified yet']
};
const planner=$('#hardware-planner');
if(planner){
 const update=()=>{const record=transportNotes[$('#plan-transport').value];$('#plan-heading').textContent=record[0];$('#plan-copy').textContent=record[1]+($('#plan-compute').value==='Raspberry Pi 5'?'':' The selected computer is not the reference Pi 5 configuration; include it for assessment.');const a=planner.querySelector('a[href^="apply.html"]');a.href='apply.html?'+new URLSearchParams({adapter:record[2],hardware:$('#plan-compute').value});};
 planner.addEventListener('change',update);planner.addEventListener('submit',e=>e.preventDefault());update();
 $('#download-setup')?.addEventListener('click',()=>{const r=transportNotes[$('#plan-transport').value];download('DRIFTER-setup-checklist.md',['# DRIFTER VIM — Setup checklist','','This is a user-selected setup, not verified compatibility.','','## Selected setup','- Computer: '+$('#plan-compute').value,'- Vehicle connection: '+r[2],'',r[1],'','## Record before the first test','- Vehicle year, make, model and engine / powertrain.','- Exact OBD adapter model or clone description.','- Display, storage and intended vehicle power path.','- DRIFTER commit or release used for the test.','- Negotiated protocol, when known.','','## Test evidence to collect','- Touchscreen onboarding and separate adapter / ECU proof.','- Ten cold starts from the intended supply.','- A 30-minute supported telemetry session.','- Adapter recovery and display recovery.','- An incident bundle from manual capture.','','No VIN or credentials are needed in the public application.','Contact: '+email].join('\n'),'text/markdown;charset=utf-8');});
}
$$('[data-filter]').forEach(b=>b.addEventListener('click',()=>{$$('[data-filter]').forEach(t=>t.setAttribute('aria-pressed',String(t===b)));$$('[data-category]').forEach(row=>row.hidden=b.dataset.filter!=='all'&&b.dataset.filter!==row.dataset.category);}));

const report=$('#field-report');
if(report){
 const field=id=>$('#'+id).value.trim(),labels={'not-tested':'Not tested',pass:'Pass',fail:'Fail','not-applicable':'Not applicable'};
 const selected=()=>$$('[data-outcome]').filter(n=>n.value!=='not-tested').length;
 function counts(){const n=selected();$('#report-count').textContent=n+' / 7 outcomes recorded';}
 report.addEventListener('input',e=>{if(typeof e.target.setCustomValidity==='function')e.target.setCustomValidity('');$('#report-message').classList.remove('show');counts();});report.addEventListener('change',counts);report.addEventListener('submit',e=>e.preventDefault());
 function validate(){
  for(const id of ['report-vehicle','report-adapter','report-build','report-node'])$('#'+id).setCustomValidity(field(id)?'':'Describe the tested setup; spaces alone are not a value.');
  const tried=$('#boots-attempted'),passed=$('#boots-passed');tried.setCustomValidity('');passed.setCustomValidity('');
  if(passed.value!==''&&(tried.value===''||Number(passed.value)>Number(tried.value)))passed.setCustomValidity('Successful starts cannot exceed attempted starts.');
  if($('#result-cold-boots').value==='pass'&&(tried.value===''||passed.value===''||Number(tried.value)<10||Number(passed.value)!==Number(tried.value)))passed.setCustomValidity('A cold-start pass needs at least 10 successful starts and no failures in the recorded attempts.');
  for(const select of $$('[data-outcome]')){const id=select.id.replace('result-',''),note=$('#note-'+id);note.setCustomValidity(select.value==='not-tested'||note.value.trim()?'':'Add an observation or reason for this selected outcome.');}
  if(!report.reportValidity())return false;
  if(!selected()){announce('#report-message','Record at least one outcome and its observation before exporting. Nothing is automatically marked as passed.');return false;}return true;
 }
 function record(){return {schema_version:1,website_edition:'Field Recorder 01.2',classification:'USER-REPORTED — not independently validated',test_date:field('report-date'),vehicle:field('report-vehicle'),adapter:field('report-adapter'),node:field('report-node'),drifter_build:field('report-build'),negotiated_protocol:field('report-protocol')||'Not recorded',cold_starts:{attempted:field('boots-attempted')===''?null:Number(field('boots-attempted')),successful:field('boots-passed')===''?null:Number(field('boots-passed'))},outcomes:$$('[data-outcome]').map(select=>({test:select.closest('.test-row').querySelector('h3').textContent,status:select.value,observation:$('#note-'+select.id.replace('result-','')).value.trim()}))};}
 function markdown(r){return ['# DRIFTER VIM — Field report','',r.classification,'','Test date: '+r.test_date,'Vehicle: '+r.vehicle,'Adapter: '+r.adapter,'Node / display / power: '+r.node,'DRIFTER build: '+r.drifter_build,'Protocol: '+r.negotiated_protocol,'','Cold starts attempted: '+(r.cold_starts.attempted??'Not recorded'),'Cold starts successful: '+(r.cold_starts.successful??'Not recorded'),'',...r.outcomes.flatMap(t=>['## '+t.test,'Outcome: '+labels[t.status],'Observation: '+(t.observation||'Not recorded'), '']),'This report records the author’s observations. It is not official compatibility or release sign-off.','Review for private identifiers before sharing.'].join('\n');}
 $('#export-markdown').addEventListener('click',()=>{if(!validate())return;const r=record();download('DRIFTER-field-report-'+r.test_date+'.md',markdown(r),'text/markdown;charset=utf-8');announce('#report-message','Report downloaded. It is labelled user-reported, not independently validated.');});
 $('#export-json').addEventListener('click',()=>{if(!validate())return;const r=record();download('DRIFTER-field-report-'+r.test_date+'.json',JSON.stringify(r,null,2),'application/json');announce('#report-message','JSON report downloaded. Nothing has been submitted to a server.');});
 $('#print-report').addEventListener('click',()=>{
  if(!validate())return;
  let output=$('#report-printout');
  if(!output){output=document.createElement('article');output.id='report-printout';const pre=document.createElement('pre');output.appendChild(pre);document.body.appendChild(output);}
  output.querySelector('pre').textContent=markdown(record());
  document.documentElement.classList.add('printing-field-report');window.print();
 });
 window.addEventListener('afterprint',()=>document.documentElement.classList.remove('printing-field-report'));
 counts();
}

const media=$('#media-sources');
let sourceMap={};if(media){try{sourceMap=JSON.parse(media.textContent);}catch{announce('#asset-message','Asset metadata could not be read. The original SVG links remain available.');}}
$$('[data-png]').forEach(button=>{button.disabled=false;button.addEventListener('click',async()=>{
 const name=button.dataset.png,source=sourceMap[name];if(!source){announce('#asset-message','This artwork is not available for PNG conversion. Use its SVG download.');return;}
 button.disabled=true;let url;
 try{const parsed=new DOMParser().parseFromString(source,'image/svg+xml'),svg=parsed.documentElement;if(parsed.querySelector('parsererror'))throw Error('Invalid SVG');const w=Number(svg.getAttribute('width')),h=Number(svg.getAttribute('height'));if(!(w>0&&h>0&&w<=4096&&h<=4096))throw Error('Unsupported dimensions');const img=new Image();url=URL.createObjectURL(new Blob([source],{type:'image/svg+xml;charset=utf-8'}));await new Promise((res,rej)=>{img.onload=res;img.onerror=rej;img.src=url;});const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;canvas.getContext('2d').drawImage(img,0,0);const blob=await new Promise(res=>canvas.toBlob(res,'image/png'));if(!blob)throw Error('PNG conversion failed');download(name.replace(/\.svg$/,'.png'),blob);announce('#asset-message','PNG prepared at '+w+' × '+h+' pixels.');}
 catch{announce('#asset-message','PNG export was unavailable in this browser. Download the original SVG instead.');}
 finally{button.disabled=false;if(url)URL.revokeObjectURL(url);}
 });});
})();
