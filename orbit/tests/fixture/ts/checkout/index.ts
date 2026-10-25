import { Cart } from './cart'
import type { Money } from '@fixture/money'
export { Rule } from './rule'
const fs = require('fs')

// Comment mentioning Phantom must not be a ref
export class CartView extends Component implements Renderable, Disposable {
  total(): Money { return new Cart().sum() }
}
export interface Renderable { render(): void }
type Alias = string
