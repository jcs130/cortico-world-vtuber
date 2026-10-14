/** Offline: prepare host model input, or validate an already generated plan. */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { prepareArtContext, validateArtPlan, type ArtRequest } from '../src/art-brief.ts';

const args=process.argv.slice(2);
const imageTokens=args.find(arg=>arg.startsWith('--image-tokens='))?.slice('--image-tokens='.length);
const [input, output, generatedPlan] = args.filter(arg=>!arg.startsWith('--image-tokens='));
if (!input || !output) throw new Error('Usage: tsx scripts/prepare-art.ts request.json output-directory [generated-plan.json] [--image-tokens=N]');
const request=JSON.parse(readFileSync(resolve(input),'utf8').replace(/^\uFEFF/,'')) as ArtRequest;
const out=resolve(output);mkdirSync(out,{recursive:true});
const prepared=prepareArtContext(request,{contextTokens:32768,reservedOutputTokens:1400,imageInputTokens:imageTokens===undefined?undefined:Number(imageTokens)});
writeFileSync(join(out,'request-original.json'),JSON.stringify(request,null,2));
writeFileSync(join(out,'planner-context.json'),JSON.stringify(prepared,null,2));
if (generatedPlan) {
  const plan=validateArtPlan(JSON.parse(readFileSync(resolve(generatedPlan),'utf8').replace(/^\uFEFF/,'')),request);
  writeFileSync(join(out,'plan-validated.json'),JSON.stringify(plan,null,2));
}
console.log(JSON.stringify({output:out,mode:request.mode,inputBudget:prepared.budget,images:prepared.images.length,networkCalls:0,modelCalls:0}));
