export class Agent {
  subscribers: unknown[] = []
  requestedPollIntervals = new Map<string, number>()
  async send(subscriber: unknown, id: string, navigation: any, api: any) {
    await this.chatClient.call(chat.bsky.convo.sendMessage, {convoId: id})
    await api.get('/users')
    this.subscribers.push(subscriber)
    this.requestedPollIntervals.delete(id)
    navigation.push('Profile', {id})
    navigation.navigate('Home')
  }
}
export const groupChats = (ax: any) => ax.features.enabled(ax.features.GroupChatsDisable)
