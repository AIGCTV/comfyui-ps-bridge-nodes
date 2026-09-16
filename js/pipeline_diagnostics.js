/** Explicitly enabled, bounded timings. Never retain output data or URLs. */
const fields=new Set(["runId","promptId","nodeId","sequence","resultId","batchIndex","count","byteSize","attempt"]);
const stages=new Set(["editor.received","editor.resource.started","editor.resource.completed",
  "editor.resource.failed","editor.resource.cancelled","editor.resource.recovering",
  "editor.output.dispatched","editor.node.dispatched","editor.image.committed"]);
export class PipelineDiagnostics {
  constructor(){this.enabled=false;this.records=[];}
  start(){this.records=[];this.enabled=true;}
  stop(){this.enabled=false;}
  snapshot(){return this.records.map(record=>({...record}));}
  record(value){
    if(!this.enabled||!stages.has(value?.stage))return;
    const record={stage:value.stage,atMs:Date.now()};
    for(const key of fields){
      const field=value[key];
      if(typeof field==="string"&&field.length<=256||typeof field==="number"&&Number.isFinite(field))record[key]=field;
    }
    if(this.records.length>=2048)this.records.shift();
    this.records.push(record);
  }
}
export const pipelineDiagnostics=new PipelineDiagnostics();
