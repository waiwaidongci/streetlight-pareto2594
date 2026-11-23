/** @typedef {{key:string,label:string,type:'string'|'int'|'number'|'date'|'select'|'bool',required?:boolean,min?:number,max?:number,default?:unknown,options?:string[],help?:string,max_length?:number}} Field */
/** @typedef {{title:string,short_name:string,page:string,run:string,handover:string,decision:string,min_days:number,max_days:number,params:Field[],modes:string[],sample_name:string,default_dataset:string}} SegmentSpec */
/** @typedef {{title:string,short_name:string,subtitle:string,organization:string,workflow:string,pages:string[],fields:Field[],params:Field[],methodology:string[],modes:string[],segment:SegmentSpec}} ProjectSpec */
export {};
