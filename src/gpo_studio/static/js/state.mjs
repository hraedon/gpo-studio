export const state={gpos:[],current:null,validation:[],policyHash:"",side:"all",editingSetting:null,editingLink:null,editingFilter:null};
export const $=selector=>document.querySelector(selector);
export const $$=selector=>[...document.querySelectorAll(selector)];
export const escapeHtml=value=>String(value??"").replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
