/** @typedef {{id:number,code:string,weekly_reading:number,completion:number,night_share:number,note:string}} Reader */
/** @typedef {{id:number,name:string,k:number,features:string[],state:string,result:object,origin:string,origin_id:number|null}} Segment */
/** @typedef {{id:number,segment_id:number,cluster_id:number,title:string,note:string,members:string[],status:string,origin:string,origin_id:number|null}} Outreach */
/** @typedef {{id:number,bundle_id:string,source:string,state:'pending'|'stored'|'rejected',preview:{segments:{added:object[],reused:object[],conflicts:object[]},outreaches:{added:object[],reused:object[],conflicts:object[]}}|null,error:string,created:string}} Bundle */
