export type Account = {id:string; username:string; paused:boolean; mode:"mock"|"live"};
export type Relationship = {id:string; account_id:string; target_id:string; username:string; protected:boolean; excluded:boolean; managed:boolean; ever_followed:boolean; observed_state:string; version:number};
export type Job = {id:string; account_id:string; target_id:string|null; kind:string; state:string; scheduled_at:string};
export class ApiError extends Error {constructor(public status:number, message:string){super(message);}}
export async function api<T>(path:string, init?:RequestInit):Promise<T>{
 const res=await fetch(`/api/v1${path}`,{...init,credentials:"same-origin",headers:{"Content-Type":"application/json",...init?.headers},cache:"no-store"});
 if(!res.ok){const body=await res.json().catch(()=>({detail:res.statusText}));throw new ApiError(res.status,String(body.error?.message ?? body.detail));}
 if (res.status === 204) return undefined as T;
 return res.json() as Promise<T>;
}
