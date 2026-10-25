import {bus} from '../sync/bus'
export const CONVO_KEY = (id: string) => ['convo', id]
export function apply(qc: any, id: string, v: unknown) { bus.on(null); qc.setQueryData(CONVO_KEY(id), v) }
