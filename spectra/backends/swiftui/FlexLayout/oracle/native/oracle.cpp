// Oracle built from React Native's vendored Yoga (the exact C++ RN 0.76.5 ships), so parity means parity with RN.
// stdin, per case: "case <id> <width> <height|nan> <nodes>", then one line per node in preorder:
//   <childCount> <measure: none|text n|fixed w h> <key> <value> ... ;
// stdout, per case: "case <id>" then "<left> <top> <width> <height>" per node in preorder.
#include <yoga/Yoga.h>
#include <cmath>
#include <iostream>
#include <map>
#include <string>
#include <vector>

struct Measure { std::string kind; double n = 0, w = 0, h = 0; };

static YGSize measure(YGNodeConstRef node, float width, YGMeasureMode wm, float, YGMeasureMode) {
  auto* m = static_cast<Measure*>(YGNodeGetContext(node));
  if (m->kind == "fixed") return {float(m->w), float(m->h)};
  // Mirror of measureText in gen.mjs.
  if (wm == YGMeasureModeUndefined || std::isnan(width)) return {float(m->n * 7), 17};
  double cpl = std::min(m->n, std::max(1.0, std::floor(double(width) / 7)));
  double lines = std::ceil(m->n / cpl);
  return {wm == YGMeasureModeExactly ? width : float(cpl * 7), float(lines * 17)};
}

static YGNodeRef readNode(YGConfigRef config, std::vector<Measure*>& measures) {
  YGNodeRef y = YGNodeNewWithConfig(config);
  int childCount; std::string kind; std::cin >> childCount >> kind;
  if (kind != "none") {
    auto* m = new Measure{kind};
    if (kind == "text") std::cin >> m->n; else std::cin >> m->w >> m->h;
    measures.push_back(m);
    YGNodeSetContext(y, m);
    YGNodeSetMeasureFunc(y, measure);
  }
  static const std::map<std::string, YGEdge> edges = {{"Top", YGEdgeTop}, {"Right", YGEdgeRight},
    {"Bottom", YGEdgeBottom}, {"Left", YGEdgeLeft}};
  static const std::map<std::string, YGJustify> justify = {{"flex-start", YGJustifyFlexStart},
    {"center", YGJustifyCenter}, {"flex-end", YGJustifyFlexEnd}, {"space-between", YGJustifySpaceBetween},
    {"space-around", YGJustifySpaceAround}, {"space-evenly", YGJustifySpaceEvenly}};
  static const std::map<std::string, YGAlign> align = {{"stretch", YGAlignStretch}, {"flex-start", YGAlignFlexStart},
    {"center", YGAlignCenter}, {"flex-end", YGAlignFlexEnd}, {"auto", YGAlignAuto}};
  for (std::string k; std::cin >> k && k != ";";) {
    std::string v; std::cin >> v;
    auto f = [&] { return std::stof(v); };
    if (k == "flexDirection") YGNodeStyleSetFlexDirection(y, v == "row" ? YGFlexDirectionRow : YGFlexDirectionColumn);
    else if (k == "justifyContent") YGNodeStyleSetJustifyContent(y, justify.at(v));
    else if (k == "alignItems") YGNodeStyleSetAlignItems(y, align.at(v));
    else if (k == "alignSelf") YGNodeStyleSetAlignSelf(y, align.at(v));
    else if (k == "flexGrow") YGNodeStyleSetFlexGrow(y, f());
    else if (k == "flexShrink") YGNodeStyleSetFlexShrink(y, f());
    else if (k == "flex") YGNodeStyleSetFlex(y, f());
    else if (k == "flexBasis") YGNodeStyleSetFlexBasis(y, f());
    else if (k == "width") YGNodeStyleSetWidth(y, f());
    else if (k == "height") YGNodeStyleSetHeight(y, f());
    else if (k == "minWidth") YGNodeStyleSetMinWidth(y, f());
    else if (k == "maxWidth") YGNodeStyleSetMaxWidth(y, f());
    else if (k == "minHeight") YGNodeStyleSetMinHeight(y, f());
    else if (k == "maxHeight") YGNodeStyleSetMaxHeight(y, f());
    else if (k == "rowGap") YGNodeStyleSetGap(y, YGGutterRow, f());
    else if (k == "columnGap") YGNodeStyleSetGap(y, YGGutterColumn, f());
    else if (k == "position") YGNodeStyleSetPositionType(y, v == "absolute" ? YGPositionTypeAbsolute : YGPositionTypeRelative);
    else if (k.rfind("padding", 0) == 0) YGNodeStyleSetPadding(y, edges.at(k.substr(7)), f());
    else if (k.rfind("margin", 0) == 0) YGNodeStyleSetMargin(y, edges.at(k.substr(6)), f());
    else if (k == "top" || k == "right" || k == "bottom" || k == "left") {
      std::string e = k; e[0] = char(toupper(e[0])); YGNodeStyleSetPosition(y, edges.at(e), f());
    } else { std::cerr << "unknown key " << k << "\n"; std::exit(2); }
  }
  for (int i = 0; i < childCount; i++) YGNodeInsertChild(y, readNode(config, measures), size_t(i));
  return y;
}

static void frames(YGNodeRef y) {
  std::cout << YGNodeLayoutGetLeft(y) << ' ' << YGNodeLayoutGetTop(y) << ' ' << YGNodeLayoutGetWidth(y) << ' '
            << YGNodeLayoutGetHeight(y) << '\n';
  for (size_t i = 0; i < YGNodeGetChildCount(y); i++) frames(YGNodeGetChild(y, i));
}

int main() {
  YGConfigRef config = YGConfigNew();
  YGConfigSetErrata(config, YGErrataAll);  // RN default: YogaLayoutableShadowNode.cpp defaultErrata
  YGConfigSetPointScaleFactor(config, 0);
  std::cout.precision(9);
  for (std::string tag; std::cin >> tag && tag == "case";) {
    long id; std::string ws, hs; int nodes; std::cin >> id >> ws >> hs >> nodes;
    std::vector<Measure*> measures;
    YGNodeRef root = readNode(config, measures);
    YGNodeCalculateLayout(root, std::stof(ws), hs == "nan" ? YGUndefined : std::stof(hs), YGDirectionLTR);
    std::cout << "case " << id << '\n';
    frames(root);
    YGNodeFreeRecursive(root);
    for (auto* m : measures) delete m;
  }
}
