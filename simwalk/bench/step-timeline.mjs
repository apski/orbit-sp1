import { connect } from '../driver.mjs';
const d = await connect(process.argv[2]);
await d.tap({ id: 'com.apple.settings.general' }, { until: { id: 'About' } }).catch(()=>{});
for (let k=0;k<3;k++){
// on General: tap About, record timeline of tree states
let t0=performance.now(); const f0=JSON.stringify((await d.find({id:'About'})).el.frame);
const r=await d.find({id:'About'}); await d.tapXY(r.el.tapPoint.x, r.el.tapPoint.y); const tTap=performance.now()-t0;
let seen=-1, stable=-1, last;
for(;;){ const x=await d.find('iOS Version'); const now=performance.now()-t0; if(x&&seen<0) seen=now; const f=x&&JSON.stringify(x.el.frame); if(x&&f===last){stable=now;break;} last=f; }
console.log(`tap sent ${tTap.toFixed(0)}ms | 'iOS Version' in tree ${seen.toFixed(0)}ms | frame settled ${stable.toFixed(0)}ms`);
t0=performance.now(); await d.tap({ id: 'BackButton' }, { until: { id: 'SCREEN_CAPTURE' } }); 
let s2; for(let last2;;){const x=await d.find({id:'About'}); const f=x&&JSON.stringify(x.el.frame); if(x&&f===last2){s2=performance.now()-t0;break;} last2=f;}
console.log(`back: until-seen+settled ${s2.toFixed(0)}ms`);
}
d.close(); process.exit(0);
