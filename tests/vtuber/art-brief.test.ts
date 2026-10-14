import { describe, expect, it } from 'vitest';
import { ART_PLANNER_SYSTEM, prepareArtContext, validateArtPlan, type ArtRequest } from '../../src/art-brief.ts';

const fact = (id: string, source: 'game-state' | 'visual-observation', value: string, observedAt='2026-10-14T08:00:00Z') => ({ id, key: 'held-item', source, observedAt, value });
const request = (): ArtRequest => ({ schema:'agent-art-request/v1', requestId:'a1', mode:'experience', idea:'Paint a beautiful moment at camp', character:{id:'cormy',description:'Cream hair, amber eyes, cat ears, light coat'}, scene:{id:'camp',facts:[fact('f1','game-state','cobblestone')]}});
const budget = {contextTokens:20000,reservedOutputTokens:1200};
const plan = () => ({schema:'agent-art-plan/v1',requestId:'a1',mode:'experience',title:'Camp',intent:'Artwork of a recorded episode',promptEn:'A quiet photographic camp scene with a cat-eared adventurer.',caption:'Camp, interpreted as artwork',basisFactIds:['f1'],creativeChoices:['New camera and light']});

describe('artwork planning evidence and host boundary', () => {
  it('keeps the system prefix stable across real and imagined requests', () => {
    const a=prepareArtContext(request(),budget);
    const b=prepareArtContext({...request(),mode:'imagination',scene:undefined,idea:'Invent the scene she wants to paint'},budget);
    expect(a.system).toBe(ART_PLANNER_SYSTEM);expect(b.system).toBe(a.system);expect(a.user).not.toBe(b.user);
  });
  it('supports imagination without a screenshot or Minecraft state', () => {
    const out=prepareArtContext({...request(),mode:'imagination',scene:undefined},budget);
    expect(out.images).toEqual([]);expect(out.sourceFactIds).toEqual([]);expect(out.user).toContain('not a claim of real occurrence');
  });
  it('requires real evidence for experience mode', () => {
    expect(()=>prepareArtContext({...request(),scene:undefined},budget)).toThrow('recorded scene facts');
  });
  it('can paint a landscape without inserting the host character', () => {
    expect(()=>prepareArtContext({...request(),mode:'imagination',character:undefined,scene:undefined,preferences:{includeCharacter:false}},budget)).not.toThrow();
  });
  it('retains conflicting image and game evidence with timestamps', () => {
    const r=request();r.scene!.facts.push(fact('v1','visual-observation','empty hand'));
    const out=prepareArtContext(r,budget);
    expect(out.sourceFactIds).toEqual(['f1','v1']);expect(out.user).toContain('empty hand');expect(out.user).toContain('cobblestone');
  });
  it('replaces only older facts with the same key and source', () => {
    const r=request();r.scene!.facts.push(fact('f2','game-state','pickaxe','2026-10-14T08:01:00Z'));
    expect(prepareArtContext(r,budget).sourceFactIds).toEqual(['f2']);expect(r.scene!.facts).toHaveLength(2);
  });
  it('does not silently discard a same-time conflict', () => {
    const r=request();r.scene!.facts.push(fact('f2','game-state','pickaxe'));
    expect(prepareArtContext(r,budget).sourceFactIds).toEqual(['f1','f2']);
  });
  it('a newer observation replaces all alternatives from an older timestamp', () => {
    const r=request();r.scene!.facts.push(fact('f2','game-state','pickaxe'),fact('f3','game-state','seeds','2026-10-14T08:02:00Z'));
    expect(prepareArtContext(r,budget).sourceFactIds).toEqual(['f3']);
    expect(()=>validateArtPlan(plan(),r)).toThrow('unknown source fact');
  });
  it('charges tool descriptions and output reserve against the input budget', () => {
    expect(()=>prepareArtContext(request(),{contextTokens:12000,reservedOutputTokens:1200,toolDescriptions:'t'.repeat(12000)})).toThrow('exceed context budget');
  });
  it('lets the host supply its actual tokenizer', () => {
    const out=prepareArtContext(request(),{...budget,countTokens:()=>7});
    expect(out.budget).toMatchObject({inputTokens:21,accounting:'host-tokenizer'});
  });
  it('rejects fabricated source references and mode changes', () => {
    expect(()=>validateArtPlan({...plan(),basisFactIds:['invented']},request())).toThrow('unknown source fact');
    expect(()=>validateArtPlan({...plan(),mode:'imagination'},request())).toThrow('mode mismatch');
  });
  it('accepts a creative plan with separately recorded artistic departures', () => {
    const p=validateArtPlan(plan(),request());expect(p.creativeChoices).toEqual(['New camera and light']);expect('completed' in p).toBe(false);
  });
  it('requires source provenance and dated inputs', () => {
    expect(()=>validateArtPlan({...plan(),basisFactIds:[]},request())).toThrow('must cite');
    const r=request();r.scene!.facts[0].observedAt='unknown';expect(()=>prepareArtContext(r,budget)).toThrow('timestamp');
  });
  it('rejects a Chinese paragraph accidentally placed in the English prompt field', () => {
    expect(()=>validateArtPlan({...plan(),promptEn:'可米站在午间营地里，观察旁边的铁轨和花朵。'},request())).toThrow('standalone English');
  });
});
