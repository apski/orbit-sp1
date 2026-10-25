import Foundation

public struct FeedReducer: Reducer, Equatable {
    var items: [FeedItem] = []
}
enum FeedAction { case load }
