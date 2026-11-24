export interface GenerationNotice {
  sessionId:string;
  attemptId:string;
  reason:string;
  stoppedAt:number;
}

/** Owner-only status payload. No prompts, document contents or credentials. */
export class GenerationNotices {
  private entries:GenerationNotice[]=[];
  private capacity:number;
  constructor(capacity=20){this.capacity=capacity;}
  add(notice:GenerationNotice):void {
    if(this.entries.some(n=>n.sessionId===notice.sessionId&&n.attemptId===notice.attemptId&&n.stoppedAt===notice.stoppedAt))return;
    this.entries.push({...notice});
    this.entries=this.entries.slice(-this.capacity);
  }
  list():GenerationNotice[]{return this.entries.map(n=>({...n}));}
}
