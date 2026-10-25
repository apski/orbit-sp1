import {useConvo} from '../../state/f/core'
import {Row} from './Row'
export function Screen() { return Row(useConvo()) }
