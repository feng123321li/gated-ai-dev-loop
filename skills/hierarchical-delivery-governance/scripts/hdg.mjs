#!/usr/bin/env node
import { createRequire as __createRequire } from 'node:module';
const require = __createRequire(import.meta.url);
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __require = /* @__PURE__ */ ((x) => typeof require !== "undefined" ? require : typeof Proxy !== "undefined" ? new Proxy(x, {
  get: (a, b) => (typeof require !== "undefined" ? require : a)[b]
}) : x)(function(x) {
  if (typeof require !== "undefined") return require.apply(this, arguments);
  throw Error('Dynamic require of "' + x + '" is not supported');
});
var __commonJS = (cb, mod) => function __require2() {
  try {
    return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
  } catch (e) {
    throw mod = 0, e;
  }
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));

// node_modules/yaml/dist/nodes/identity.js
var require_identity = __commonJS({
  "node_modules/yaml/dist/nodes/identity.js"(exports) {
    "use strict";
    var ALIAS = /* @__PURE__ */ Symbol.for("yaml.alias");
    var DOC = /* @__PURE__ */ Symbol.for("yaml.document");
    var MAP = /* @__PURE__ */ Symbol.for("yaml.map");
    var PAIR = /* @__PURE__ */ Symbol.for("yaml.pair");
    var SCALAR = /* @__PURE__ */ Symbol.for("yaml.scalar");
    var SEQ = /* @__PURE__ */ Symbol.for("yaml.seq");
    var NODE_TYPE = /* @__PURE__ */ Symbol.for("yaml.node.type");
    var isAlias = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === ALIAS;
    var isDocument = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === DOC;
    var isMap = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === MAP;
    var isPair = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === PAIR;
    var isScalar = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === SCALAR;
    var isSeq = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === SEQ;
    function isCollection(node) {
      if (node && typeof node === "object")
        switch (node[NODE_TYPE]) {
          case MAP:
          case SEQ:
            return true;
        }
      return false;
    }
    function isNode(node) {
      if (node && typeof node === "object")
        switch (node[NODE_TYPE]) {
          case ALIAS:
          case MAP:
          case SCALAR:
          case SEQ:
            return true;
        }
      return false;
    }
    var hasAnchor = (node) => (isScalar(node) || isCollection(node)) && !!node.anchor;
    exports.ALIAS = ALIAS;
    exports.DOC = DOC;
    exports.MAP = MAP;
    exports.NODE_TYPE = NODE_TYPE;
    exports.PAIR = PAIR;
    exports.SCALAR = SCALAR;
    exports.SEQ = SEQ;
    exports.hasAnchor = hasAnchor;
    exports.isAlias = isAlias;
    exports.isCollection = isCollection;
    exports.isDocument = isDocument;
    exports.isMap = isMap;
    exports.isNode = isNode;
    exports.isPair = isPair;
    exports.isScalar = isScalar;
    exports.isSeq = isSeq;
  }
});

// node_modules/yaml/dist/visit.js
var require_visit = __commonJS({
  "node_modules/yaml/dist/visit.js"(exports) {
    "use strict";
    var identity = require_identity();
    var BREAK = /* @__PURE__ */ Symbol("break visit");
    var SKIP = /* @__PURE__ */ Symbol("skip children");
    var REMOVE = /* @__PURE__ */ Symbol("remove node");
    function visit(node, visitor) {
      const visitor_ = initVisitor(visitor);
      if (identity.isDocument(node)) {
        const cd = visit_(null, node.contents, visitor_, Object.freeze([node]));
        if (cd === REMOVE)
          node.contents = null;
      } else
        visit_(null, node, visitor_, Object.freeze([]));
    }
    visit.BREAK = BREAK;
    visit.SKIP = SKIP;
    visit.REMOVE = REMOVE;
    function visit_(key, node, visitor, path16) {
      const ctrl = callVisitor(key, node, visitor, path16);
      if (identity.isNode(ctrl) || identity.isPair(ctrl)) {
        replaceNode(key, path16, ctrl);
        return visit_(key, ctrl, visitor, path16);
      }
      if (typeof ctrl !== "symbol") {
        if (identity.isCollection(node)) {
          path16 = Object.freeze(path16.concat(node));
          for (let i = 0; i < node.items.length; ++i) {
            const ci = visit_(i, node.items[i], visitor, path16);
            if (typeof ci === "number")
              i = ci - 1;
            else if (ci === BREAK)
              return BREAK;
            else if (ci === REMOVE) {
              node.items.splice(i, 1);
              i -= 1;
            }
          }
        } else if (identity.isPair(node)) {
          path16 = Object.freeze(path16.concat(node));
          const ck = visit_("key", node.key, visitor, path16);
          if (ck === BREAK)
            return BREAK;
          else if (ck === REMOVE)
            node.key = null;
          const cv = visit_("value", node.value, visitor, path16);
          if (cv === BREAK)
            return BREAK;
          else if (cv === REMOVE)
            node.value = null;
        }
      }
      return ctrl;
    }
    async function visitAsync(node, visitor) {
      const visitor_ = initVisitor(visitor);
      if (identity.isDocument(node)) {
        const cd = await visitAsync_(null, node.contents, visitor_, Object.freeze([node]));
        if (cd === REMOVE)
          node.contents = null;
      } else
        await visitAsync_(null, node, visitor_, Object.freeze([]));
    }
    visitAsync.BREAK = BREAK;
    visitAsync.SKIP = SKIP;
    visitAsync.REMOVE = REMOVE;
    async function visitAsync_(key, node, visitor, path16) {
      const ctrl = await callVisitor(key, node, visitor, path16);
      if (identity.isNode(ctrl) || identity.isPair(ctrl)) {
        replaceNode(key, path16, ctrl);
        return visitAsync_(key, ctrl, visitor, path16);
      }
      if (typeof ctrl !== "symbol") {
        if (identity.isCollection(node)) {
          path16 = Object.freeze(path16.concat(node));
          for (let i = 0; i < node.items.length; ++i) {
            const ci = await visitAsync_(i, node.items[i], visitor, path16);
            if (typeof ci === "number")
              i = ci - 1;
            else if (ci === BREAK)
              return BREAK;
            else if (ci === REMOVE) {
              node.items.splice(i, 1);
              i -= 1;
            }
          }
        } else if (identity.isPair(node)) {
          path16 = Object.freeze(path16.concat(node));
          const ck = await visitAsync_("key", node.key, visitor, path16);
          if (ck === BREAK)
            return BREAK;
          else if (ck === REMOVE)
            node.key = null;
          const cv = await visitAsync_("value", node.value, visitor, path16);
          if (cv === BREAK)
            return BREAK;
          else if (cv === REMOVE)
            node.value = null;
        }
      }
      return ctrl;
    }
    function initVisitor(visitor) {
      if (typeof visitor === "object" && (visitor.Collection || visitor.Node || visitor.Value)) {
        return Object.assign({
          Alias: visitor.Node,
          Map: visitor.Node,
          Scalar: visitor.Node,
          Seq: visitor.Node
        }, visitor.Value && {
          Map: visitor.Value,
          Scalar: visitor.Value,
          Seq: visitor.Value
        }, visitor.Collection && {
          Map: visitor.Collection,
          Seq: visitor.Collection
        }, visitor);
      }
      return visitor;
    }
    function callVisitor(key, node, visitor, path16) {
      if (typeof visitor === "function")
        return visitor(key, node, path16);
      if (identity.isMap(node))
        return visitor.Map?.(key, node, path16);
      if (identity.isSeq(node))
        return visitor.Seq?.(key, node, path16);
      if (identity.isPair(node))
        return visitor.Pair?.(key, node, path16);
      if (identity.isScalar(node))
        return visitor.Scalar?.(key, node, path16);
      if (identity.isAlias(node))
        return visitor.Alias?.(key, node, path16);
      return void 0;
    }
    function replaceNode(key, path16, node) {
      const parent = path16[path16.length - 1];
      if (identity.isCollection(parent)) {
        parent.items[key] = node;
      } else if (identity.isPair(parent)) {
        if (key === "key")
          parent.key = node;
        else
          parent.value = node;
      } else if (identity.isDocument(parent)) {
        parent.contents = node;
      } else {
        const pt = identity.isAlias(parent) ? "alias" : "scalar";
        throw new Error(`Cannot replace node with ${pt} parent`);
      }
    }
    exports.visit = visit;
    exports.visitAsync = visitAsync;
  }
});

// node_modules/yaml/dist/doc/directives.js
var require_directives = __commonJS({
  "node_modules/yaml/dist/doc/directives.js"(exports) {
    "use strict";
    var identity = require_identity();
    var visit = require_visit();
    var escapeChars = {
      "!": "%21",
      ",": "%2C",
      "[": "%5B",
      "]": "%5D",
      "{": "%7B",
      "}": "%7D"
    };
    var escapeTagName = (tn) => tn.replace(/[!,[\]{}]/g, (ch) => escapeChars[ch]);
    var Directives = class _Directives {
      constructor(yaml, tags) {
        this.docStart = null;
        this.docEnd = false;
        this.yaml = Object.assign({}, _Directives.defaultYaml, yaml);
        this.tags = Object.assign({}, _Directives.defaultTags, tags);
      }
      clone() {
        const copy = new _Directives(this.yaml, this.tags);
        copy.docStart = this.docStart;
        return copy;
      }
      /**
       * During parsing, get a Directives instance for the current document and
       * update the stream state according to the current version's spec.
       */
      atDocument() {
        const res = new _Directives(this.yaml, this.tags);
        switch (this.yaml.version) {
          case "1.1":
            this.atNextDocument = true;
            break;
          case "1.2":
            this.atNextDocument = false;
            this.yaml = {
              explicit: _Directives.defaultYaml.explicit,
              version: "1.2"
            };
            this.tags = Object.assign({}, _Directives.defaultTags);
            break;
        }
        return res;
      }
      /**
       * @param onError - May be called even if the action was successful
       * @returns `true` on success
       */
      add(line, onError) {
        if (this.atNextDocument) {
          this.yaml = { explicit: _Directives.defaultYaml.explicit, version: "1.1" };
          this.tags = Object.assign({}, _Directives.defaultTags);
          this.atNextDocument = false;
        }
        const parts = line.trim().split(/[ \t]+/);
        const name = parts.shift();
        switch (name) {
          case "%TAG": {
            if (parts.length !== 2) {
              onError(0, "%TAG directive should contain exactly two parts");
              if (parts.length < 2)
                return false;
            }
            const [handle, prefix] = parts;
            this.tags[handle] = prefix;
            return true;
          }
          case "%YAML": {
            this.yaml.explicit = true;
            if (parts.length !== 1) {
              onError(0, "%YAML directive should contain exactly one part");
              return false;
            }
            const [version] = parts;
            if (version === "1.1" || version === "1.2") {
              this.yaml.version = version;
              return true;
            } else {
              const isValid = /^\d+\.\d+$/.test(version);
              onError(6, `Unsupported YAML version ${version}`, isValid);
              return false;
            }
          }
          default:
            onError(0, `Unknown directive ${name}`, true);
            return false;
        }
      }
      /**
       * Resolves a tag, matching handles to those defined in %TAG directives.
       *
       * @returns Resolved tag, which may also be the non-specific tag `'!'` or a
       *   `'!local'` tag, or `null` if unresolvable.
       */
      tagName(source, onError) {
        if (source === "!")
          return "!";
        if (source[0] !== "!") {
          onError(`Not a valid tag: ${source}`);
          return null;
        }
        if (source[1] === "<") {
          const verbatim = source.slice(2, -1);
          if (verbatim === "!" || verbatim === "!!") {
            onError(`Verbatim tags aren't resolved, so ${source} is invalid.`);
            return null;
          }
          if (source[source.length - 1] !== ">")
            onError("Verbatim tags must end with a >");
          return verbatim;
        }
        const [, handle, suffix] = source.match(/^(.*!)([^!]*)$/s);
        if (!suffix)
          onError(`The ${source} tag has no suffix`);
        const prefix = this.tags[handle];
        if (prefix) {
          try {
            return prefix + decodeURIComponent(suffix);
          } catch (error) {
            onError(String(error));
            return null;
          }
        }
        if (handle === "!")
          return source;
        onError(`Could not resolve tag: ${source}`);
        return null;
      }
      /**
       * Given a fully resolved tag, returns its printable string form,
       * taking into account current tag prefixes and defaults.
       */
      tagString(tag) {
        for (const [handle, prefix] of Object.entries(this.tags)) {
          if (tag.startsWith(prefix))
            return handle + escapeTagName(tag.substring(prefix.length));
        }
        return tag[0] === "!" ? tag : `!<${tag}>`;
      }
      toString(doc) {
        const lines = this.yaml.explicit ? [`%YAML ${this.yaml.version || "1.2"}`] : [];
        const tagEntries = Object.entries(this.tags);
        let tagNames;
        if (doc && tagEntries.length > 0 && identity.isNode(doc.contents)) {
          const tags = {};
          visit.visit(doc.contents, (_key, node) => {
            if (identity.isNode(node) && node.tag)
              tags[node.tag] = true;
          });
          tagNames = Object.keys(tags);
        } else
          tagNames = [];
        for (const [handle, prefix] of tagEntries) {
          if (handle === "!!" && prefix === "tag:yaml.org,2002:")
            continue;
          if (!doc || tagNames.some((tn) => tn.startsWith(prefix)))
            lines.push(`%TAG ${handle} ${prefix}`);
        }
        return lines.join("\n");
      }
    };
    Directives.defaultYaml = { explicit: false, version: "1.2" };
    Directives.defaultTags = { "!!": "tag:yaml.org,2002:" };
    exports.Directives = Directives;
  }
});

// node_modules/yaml/dist/doc/anchors.js
var require_anchors = __commonJS({
  "node_modules/yaml/dist/doc/anchors.js"(exports) {
    "use strict";
    var identity = require_identity();
    var visit = require_visit();
    function anchorIsValid(anchor) {
      if (/[\x00-\x19\s,[\]{}]/.test(anchor)) {
        const sa = JSON.stringify(anchor);
        const msg = `Anchor must not contain whitespace or control characters: ${sa}`;
        throw new Error(msg);
      }
      return true;
    }
    function anchorNames(root) {
      const anchors = /* @__PURE__ */ new Set();
      visit.visit(root, {
        Value(_key, node) {
          if (node.anchor)
            anchors.add(node.anchor);
        }
      });
      return anchors;
    }
    function findNewAnchor(prefix, exclude) {
      for (let i = 1; true; ++i) {
        const name = `${prefix}${i}`;
        if (!exclude.has(name))
          return name;
      }
    }
    function createNodeAnchors(doc, prefix) {
      const aliasObjects = [];
      const sourceObjects = /* @__PURE__ */ new Map();
      let prevAnchors = null;
      return {
        onAnchor: (source) => {
          aliasObjects.push(source);
          prevAnchors ?? (prevAnchors = anchorNames(doc));
          const anchor = findNewAnchor(prefix, prevAnchors);
          prevAnchors.add(anchor);
          return anchor;
        },
        /**
         * With circular references, the source node is only resolved after all
         * of its child nodes are. This is why anchors are set only after all of
         * the nodes have been created.
         */
        setAnchors: () => {
          for (const source of aliasObjects) {
            const ref = sourceObjects.get(source);
            if (typeof ref === "object" && ref.anchor && (identity.isScalar(ref.node) || identity.isCollection(ref.node))) {
              ref.node.anchor = ref.anchor;
            } else {
              const error = new Error("Failed to resolve repeated object (this should not happen)");
              error.source = source;
              throw error;
            }
          }
        },
        sourceObjects
      };
    }
    exports.anchorIsValid = anchorIsValid;
    exports.anchorNames = anchorNames;
    exports.createNodeAnchors = createNodeAnchors;
    exports.findNewAnchor = findNewAnchor;
  }
});

// node_modules/yaml/dist/doc/applyReviver.js
var require_applyReviver = __commonJS({
  "node_modules/yaml/dist/doc/applyReviver.js"(exports) {
    "use strict";
    function applyReviver(reviver, obj, key, val) {
      if (val && typeof val === "object") {
        if (Array.isArray(val)) {
          for (let i = 0, len = val.length; i < len; ++i) {
            const v0 = val[i];
            const v1 = applyReviver(reviver, val, String(i), v0);
            if (v1 === void 0)
              delete val[i];
            else if (v1 !== v0)
              val[i] = v1;
          }
        } else if (val instanceof Map) {
          for (const k of Array.from(val.keys())) {
            const v0 = val.get(k);
            const v1 = applyReviver(reviver, val, k, v0);
            if (v1 === void 0)
              val.delete(k);
            else if (v1 !== v0)
              val.set(k, v1);
          }
        } else if (val instanceof Set) {
          for (const v0 of Array.from(val)) {
            const v1 = applyReviver(reviver, val, v0, v0);
            if (v1 === void 0)
              val.delete(v0);
            else if (v1 !== v0) {
              val.delete(v0);
              val.add(v1);
            }
          }
        } else {
          for (const [k, v0] of Object.entries(val)) {
            const v1 = applyReviver(reviver, val, k, v0);
            if (v1 === void 0)
              delete val[k];
            else if (v1 !== v0)
              val[k] = v1;
          }
        }
      }
      return reviver.call(obj, key, val);
    }
    exports.applyReviver = applyReviver;
  }
});

// node_modules/yaml/dist/nodes/toJS.js
var require_toJS = __commonJS({
  "node_modules/yaml/dist/nodes/toJS.js"(exports) {
    "use strict";
    var identity = require_identity();
    function toJS(value, arg, ctx) {
      if (Array.isArray(value))
        return value.map((v, i) => toJS(v, String(i), ctx));
      if (value && typeof value.toJSON === "function") {
        if (!ctx || !identity.hasAnchor(value))
          return value.toJSON(arg, ctx);
        const data = { aliasCount: 0, count: 1, res: void 0 };
        ctx.anchors.set(value, data);
        ctx.onCreate = (res2) => {
          data.res = res2;
          delete ctx.onCreate;
        };
        const res = value.toJSON(arg, ctx);
        if (ctx.onCreate)
          ctx.onCreate(res);
        return res;
      }
      if (typeof value === "bigint" && !ctx?.keep)
        return Number(value);
      return value;
    }
    exports.toJS = toJS;
  }
});

// node_modules/yaml/dist/nodes/Node.js
var require_Node = __commonJS({
  "node_modules/yaml/dist/nodes/Node.js"(exports) {
    "use strict";
    var applyReviver = require_applyReviver();
    var identity = require_identity();
    var toJS = require_toJS();
    var NodeBase = class {
      constructor(type) {
        Object.defineProperty(this, identity.NODE_TYPE, { value: type });
      }
      /** Create a copy of this node.  */
      clone() {
        const copy = Object.create(Object.getPrototypeOf(this), Object.getOwnPropertyDescriptors(this));
        if (this.range)
          copy.range = this.range.slice();
        return copy;
      }
      /** A plain JavaScript representation of this node. */
      toJS(doc, { mapAsMap, maxAliasCount, onAnchor, reviver } = {}) {
        if (!identity.isDocument(doc))
          throw new TypeError("A document argument is required");
        const ctx = {
          anchors: /* @__PURE__ */ new Map(),
          doc,
          keep: true,
          mapAsMap: mapAsMap === true,
          mapKeyWarned: false,
          maxAliasCount: typeof maxAliasCount === "number" ? maxAliasCount : 100
        };
        const res = toJS.toJS(this, "", ctx);
        if (typeof onAnchor === "function")
          for (const { count, res: res2 } of ctx.anchors.values())
            onAnchor(res2, count);
        return typeof reviver === "function" ? applyReviver.applyReviver(reviver, { "": res }, "", res) : res;
      }
    };
    exports.NodeBase = NodeBase;
  }
});

// node_modules/yaml/dist/nodes/Alias.js
var require_Alias = __commonJS({
  "node_modules/yaml/dist/nodes/Alias.js"(exports) {
    "use strict";
    var anchors = require_anchors();
    var visit = require_visit();
    var identity = require_identity();
    var Node = require_Node();
    var toJS = require_toJS();
    var Alias = class extends Node.NodeBase {
      constructor(source) {
        super(identity.ALIAS);
        this.source = source;
        Object.defineProperty(this, "tag", {
          set() {
            throw new Error("Alias nodes cannot have tags");
          }
        });
      }
      /**
       * Resolve the value of this alias within `doc`, finding the last
       * instance of the `source` anchor before this node.
       */
      resolve(doc, ctx) {
        if (ctx?.maxAliasCount === 0)
          throw new ReferenceError("Alias resolution is disabled");
        let nodes;
        if (ctx?.aliasResolveCache) {
          nodes = ctx.aliasResolveCache;
        } else {
          nodes = [];
          visit.visit(doc, {
            Node: (_key, node) => {
              if (identity.isAlias(node) || identity.hasAnchor(node))
                nodes.push(node);
            }
          });
          if (ctx)
            ctx.aliasResolveCache = nodes;
        }
        let found = void 0;
        for (const node of nodes) {
          if (node === this)
            break;
          if (node.anchor === this.source)
            found = node;
        }
        return found;
      }
      toJSON(_arg, ctx) {
        if (!ctx)
          return { source: this.source };
        const { anchors: anchors2, doc, maxAliasCount } = ctx;
        const source = this.resolve(doc, ctx);
        if (!source) {
          const msg = `Unresolved alias (the anchor must be set before the alias): ${this.source}`;
          throw new ReferenceError(msg);
        }
        let data = anchors2.get(source);
        if (!data) {
          toJS.toJS(source, null, ctx);
          data = anchors2.get(source);
        }
        if (data?.res === void 0) {
          const msg = "This should not happen: Alias anchor was not resolved?";
          throw new ReferenceError(msg);
        }
        if (maxAliasCount >= 0) {
          data.count += 1;
          if (data.aliasCount === 0)
            data.aliasCount = getAliasCount(doc, source, anchors2);
          if (data.count * data.aliasCount > maxAliasCount) {
            const msg = "Excessive alias count indicates a resource exhaustion attack";
            throw new ReferenceError(msg);
          }
        }
        return data.res;
      }
      toString(ctx, _onComment, _onChompKeep) {
        const src = `*${this.source}`;
        if (ctx) {
          anchors.anchorIsValid(this.source);
          if (ctx.options.verifyAliasOrder && !ctx.anchors.has(this.source)) {
            const msg = `Unresolved alias (the anchor must be set before the alias): ${this.source}`;
            throw new Error(msg);
          }
          if (ctx.implicitKey)
            return `${src} `;
        }
        return src;
      }
    };
    function getAliasCount(doc, node, anchors2) {
      if (identity.isAlias(node)) {
        const source = node.resolve(doc);
        const anchor = anchors2 && source && anchors2.get(source);
        return anchor ? anchor.count * anchor.aliasCount : 0;
      } else if (identity.isCollection(node)) {
        let count = 0;
        for (const item of node.items) {
          const c = getAliasCount(doc, item, anchors2);
          if (c > count)
            count = c;
        }
        return count;
      } else if (identity.isPair(node)) {
        const kc = getAliasCount(doc, node.key, anchors2);
        const vc = getAliasCount(doc, node.value, anchors2);
        return Math.max(kc, vc);
      }
      return 1;
    }
    exports.Alias = Alias;
  }
});

// node_modules/yaml/dist/nodes/Scalar.js
var require_Scalar = __commonJS({
  "node_modules/yaml/dist/nodes/Scalar.js"(exports) {
    "use strict";
    var identity = require_identity();
    var Node = require_Node();
    var toJS = require_toJS();
    var isScalarValue = (value) => !value || typeof value !== "function" && typeof value !== "object";
    var Scalar = class extends Node.NodeBase {
      constructor(value) {
        super(identity.SCALAR);
        this.value = value;
      }
      toJSON(arg, ctx) {
        return ctx?.keep ? this.value : toJS.toJS(this.value, arg, ctx);
      }
      toString() {
        return String(this.value);
      }
    };
    Scalar.BLOCK_FOLDED = "BLOCK_FOLDED";
    Scalar.BLOCK_LITERAL = "BLOCK_LITERAL";
    Scalar.PLAIN = "PLAIN";
    Scalar.QUOTE_DOUBLE = "QUOTE_DOUBLE";
    Scalar.QUOTE_SINGLE = "QUOTE_SINGLE";
    exports.Scalar = Scalar;
    exports.isScalarValue = isScalarValue;
  }
});

// node_modules/yaml/dist/doc/createNode.js
var require_createNode = __commonJS({
  "node_modules/yaml/dist/doc/createNode.js"(exports) {
    "use strict";
    var Alias = require_Alias();
    var identity = require_identity();
    var Scalar = require_Scalar();
    var defaultTagPrefix = "tag:yaml.org,2002:";
    function findTagObject(value, tagName, tags) {
      if (tagName) {
        const match = tags.filter((t) => t.tag === tagName);
        const tagObj = match.find((t) => !t.format) ?? match[0];
        if (!tagObj)
          throw new Error(`Tag ${tagName} not found`);
        return tagObj;
      }
      return tags.find((t) => t.identify?.(value) && !t.format);
    }
    function createNode(value, tagName, ctx) {
      if (identity.isDocument(value))
        value = value.contents;
      if (identity.isNode(value))
        return value;
      if (identity.isPair(value)) {
        const map = ctx.schema[identity.MAP].createNode?.(ctx.schema, null, ctx);
        map.items.push(value);
        return map;
      }
      if (value instanceof String || value instanceof Number || value instanceof Boolean || typeof BigInt !== "undefined" && value instanceof BigInt) {
        value = value.valueOf();
      }
      const { aliasDuplicateObjects, onAnchor, onTagObj, schema, sourceObjects } = ctx;
      let ref = void 0;
      if (aliasDuplicateObjects && value && typeof value === "object") {
        ref = sourceObjects.get(value);
        if (ref) {
          ref.anchor ?? (ref.anchor = onAnchor(value));
          return new Alias.Alias(ref.anchor);
        } else {
          ref = { anchor: null, node: null };
          sourceObjects.set(value, ref);
        }
      }
      if (tagName?.startsWith("!!"))
        tagName = defaultTagPrefix + tagName.slice(2);
      let tagObj = findTagObject(value, tagName, schema.tags);
      if (!tagObj) {
        if (value && typeof value.toJSON === "function") {
          value = value.toJSON();
        }
        if (!value || typeof value !== "object") {
          const node2 = new Scalar.Scalar(value);
          if (ref)
            ref.node = node2;
          return node2;
        }
        tagObj = value instanceof Map ? schema[identity.MAP] : Symbol.iterator in Object(value) ? schema[identity.SEQ] : schema[identity.MAP];
      }
      if (onTagObj) {
        onTagObj(tagObj);
        delete ctx.onTagObj;
      }
      const node = tagObj?.createNode ? tagObj.createNode(ctx.schema, value, ctx) : typeof tagObj?.nodeClass?.from === "function" ? tagObj.nodeClass.from(ctx.schema, value, ctx) : new Scalar.Scalar(value);
      if (tagName)
        node.tag = tagName;
      else if (!tagObj.default)
        node.tag = tagObj.tag;
      if (ref)
        ref.node = node;
      return node;
    }
    exports.createNode = createNode;
  }
});

// node_modules/yaml/dist/nodes/Collection.js
var require_Collection = __commonJS({
  "node_modules/yaml/dist/nodes/Collection.js"(exports) {
    "use strict";
    var createNode = require_createNode();
    var identity = require_identity();
    var Node = require_Node();
    function collectionFromPath(schema, path16, value) {
      let v = value;
      for (let i = path16.length - 1; i >= 0; --i) {
        const k = path16[i];
        if (typeof k === "number" && Number.isInteger(k) && k >= 0) {
          const a = [];
          a[k] = v;
          v = a;
        } else {
          v = /* @__PURE__ */ new Map([[k, v]]);
        }
      }
      return createNode.createNode(v, void 0, {
        aliasDuplicateObjects: false,
        keepUndefined: false,
        onAnchor: () => {
          throw new Error("This should not happen, please report a bug.");
        },
        schema,
        sourceObjects: /* @__PURE__ */ new Map()
      });
    }
    var isEmptyPath = (path16) => path16 == null || typeof path16 === "object" && !!path16[Symbol.iterator]().next().done;
    var Collection = class extends Node.NodeBase {
      constructor(type, schema) {
        super(type);
        Object.defineProperty(this, "schema", {
          value: schema,
          configurable: true,
          enumerable: false,
          writable: true
        });
      }
      /**
       * Create a copy of this collection.
       *
       * @param schema - If defined, overwrites the original's schema
       */
      clone(schema) {
        const copy = Object.create(Object.getPrototypeOf(this), Object.getOwnPropertyDescriptors(this));
        if (schema)
          copy.schema = schema;
        copy.items = copy.items.map((it) => identity.isNode(it) || identity.isPair(it) ? it.clone(schema) : it);
        if (this.range)
          copy.range = this.range.slice();
        return copy;
      }
      /**
       * Adds a value to the collection. For `!!map` and `!!omap` the value must
       * be a Pair instance or a `{ key, value }` object, which may not have a key
       * that already exists in the map.
       */
      addIn(path16, value) {
        if (isEmptyPath(path16))
          this.add(value);
        else {
          const [key, ...rest] = path16;
          const node = this.get(key, true);
          if (identity.isCollection(node))
            node.addIn(rest, value);
          else if (node === void 0 && this.schema)
            this.set(key, collectionFromPath(this.schema, rest, value));
          else
            throw new Error(`Expected YAML collection at ${key}. Remaining path: ${rest}`);
        }
      }
      /**
       * Removes a value from the collection.
       * @returns `true` if the item was found and removed.
       */
      deleteIn(path16) {
        const [key, ...rest] = path16;
        if (rest.length === 0)
          return this.delete(key);
        const node = this.get(key, true);
        if (identity.isCollection(node))
          return node.deleteIn(rest);
        else
          throw new Error(`Expected YAML collection at ${key}. Remaining path: ${rest}`);
      }
      /**
       * Returns item at `key`, or `undefined` if not found. By default unwraps
       * scalar values from their surrounding node; to disable set `keepScalar` to
       * `true` (collections are always returned intact).
       */
      getIn(path16, keepScalar) {
        const [key, ...rest] = path16;
        const node = this.get(key, true);
        if (rest.length === 0)
          return !keepScalar && identity.isScalar(node) ? node.value : node;
        else
          return identity.isCollection(node) ? node.getIn(rest, keepScalar) : void 0;
      }
      hasAllNullValues(allowScalar) {
        return this.items.every((node) => {
          if (!identity.isPair(node))
            return false;
          const n = node.value;
          return n == null || allowScalar && identity.isScalar(n) && n.value == null && !n.commentBefore && !n.comment && !n.tag;
        });
      }
      /**
       * Checks if the collection includes a value with the key `key`.
       */
      hasIn(path16) {
        const [key, ...rest] = path16;
        if (rest.length === 0)
          return this.has(key);
        const node = this.get(key, true);
        return identity.isCollection(node) ? node.hasIn(rest) : false;
      }
      /**
       * Sets a value in this collection. For `!!set`, `value` needs to be a
       * boolean to add/remove the item from the set.
       */
      setIn(path16, value) {
        const [key, ...rest] = path16;
        if (rest.length === 0) {
          this.set(key, value);
        } else {
          const node = this.get(key, true);
          if (identity.isCollection(node))
            node.setIn(rest, value);
          else if (node === void 0 && this.schema)
            this.set(key, collectionFromPath(this.schema, rest, value));
          else
            throw new Error(`Expected YAML collection at ${key}. Remaining path: ${rest}`);
        }
      }
    };
    exports.Collection = Collection;
    exports.collectionFromPath = collectionFromPath;
    exports.isEmptyPath = isEmptyPath;
  }
});

// node_modules/yaml/dist/stringify/stringifyComment.js
var require_stringifyComment = __commonJS({
  "node_modules/yaml/dist/stringify/stringifyComment.js"(exports) {
    "use strict";
    var stringifyComment = (str) => str.replace(/^(?!$)(?: $)?/gm, "#");
    function indentComment(comment, indent) {
      if (/^\n+$/.test(comment))
        return comment.substring(1);
      return indent ? comment.replace(/^(?! *$)/gm, indent) : comment;
    }
    var lineComment = (str, indent, comment) => str.endsWith("\n") ? indentComment(comment, indent) : comment.includes("\n") ? "\n" + indentComment(comment, indent) : (str.endsWith(" ") ? "" : " ") + comment;
    exports.indentComment = indentComment;
    exports.lineComment = lineComment;
    exports.stringifyComment = stringifyComment;
  }
});

// node_modules/yaml/dist/stringify/foldFlowLines.js
var require_foldFlowLines = __commonJS({
  "node_modules/yaml/dist/stringify/foldFlowLines.js"(exports) {
    "use strict";
    var FOLD_FLOW = "flow";
    var FOLD_BLOCK = "block";
    var FOLD_QUOTED = "quoted";
    function foldFlowLines(text2, indent, mode = "flow", { indentAtStart, lineWidth = 80, minContentWidth = 20, onFold, onOverflow } = {}) {
      if (!lineWidth || lineWidth < 0)
        return text2;
      if (lineWidth < minContentWidth)
        minContentWidth = 0;
      const endStep = Math.max(1 + minContentWidth, 1 + lineWidth - indent.length);
      if (text2.length <= endStep)
        return text2;
      const folds = [];
      const escapedFolds = {};
      let end = lineWidth - indent.length;
      if (typeof indentAtStart === "number") {
        if (indentAtStart > lineWidth - Math.max(2, minContentWidth))
          folds.push(0);
        else
          end = lineWidth - indentAtStart;
      }
      let split = void 0;
      let prev = void 0;
      let overflow = false;
      let i = -1;
      let escStart = -1;
      let escEnd = -1;
      if (mode === FOLD_BLOCK) {
        i = consumeMoreIndentedLines(text2, i, indent.length);
        if (i !== -1)
          end = i + endStep;
      }
      for (let ch; ch = text2[i += 1]; ) {
        if (mode === FOLD_QUOTED && ch === "\\") {
          escStart = i;
          switch (text2[i + 1]) {
            case "x":
              i += 3;
              break;
            case "u":
              i += 5;
              break;
            case "U":
              i += 9;
              break;
            default:
              i += 1;
          }
          escEnd = i;
        }
        if (ch === "\n") {
          if (mode === FOLD_BLOCK)
            i = consumeMoreIndentedLines(text2, i, indent.length);
          end = i + indent.length + endStep;
          split = void 0;
        } else {
          if (ch === " " && prev && prev !== " " && prev !== "\n" && prev !== "	") {
            const next = text2[i + 1];
            if (next && next !== " " && next !== "\n" && next !== "	")
              split = i;
          }
          if (i >= end) {
            if (split) {
              folds.push(split);
              end = split + endStep;
              split = void 0;
            } else if (mode === FOLD_QUOTED) {
              while (prev === " " || prev === "	") {
                prev = ch;
                ch = text2[i += 1];
                overflow = true;
              }
              const j = i > escEnd + 1 ? i - 2 : escStart - 1;
              if (escapedFolds[j])
                return text2;
              folds.push(j);
              escapedFolds[j] = true;
              end = j + endStep;
              split = void 0;
            } else {
              overflow = true;
            }
          }
        }
        prev = ch;
      }
      if (overflow && onOverflow)
        onOverflow();
      if (folds.length === 0)
        return text2;
      if (onFold)
        onFold();
      let res = text2.slice(0, folds[0]);
      for (let i2 = 0; i2 < folds.length; ++i2) {
        const fold = folds[i2];
        const end2 = folds[i2 + 1] || text2.length;
        if (fold === 0)
          res = `
${indent}${text2.slice(0, end2)}`;
        else {
          if (mode === FOLD_QUOTED && escapedFolds[fold])
            res += `${text2[fold]}\\`;
          res += `
${indent}${text2.slice(fold + 1, end2)}`;
        }
      }
      return res;
    }
    function consumeMoreIndentedLines(text2, i, indent) {
      let end = i;
      let start = i + 1;
      let ch = text2[start];
      while (ch === " " || ch === "	") {
        if (i < start + indent) {
          ch = text2[++i];
        } else {
          do {
            ch = text2[++i];
          } while (ch && ch !== "\n");
          end = i;
          start = i + 1;
          ch = text2[start];
        }
      }
      return end;
    }
    exports.FOLD_BLOCK = FOLD_BLOCK;
    exports.FOLD_FLOW = FOLD_FLOW;
    exports.FOLD_QUOTED = FOLD_QUOTED;
    exports.foldFlowLines = foldFlowLines;
  }
});

// node_modules/yaml/dist/stringify/stringifyString.js
var require_stringifyString = __commonJS({
  "node_modules/yaml/dist/stringify/stringifyString.js"(exports) {
    "use strict";
    var Scalar = require_Scalar();
    var foldFlowLines = require_foldFlowLines();
    var getFoldOptions = (ctx, isBlock) => ({
      indentAtStart: isBlock ? ctx.indent.length : ctx.indentAtStart,
      lineWidth: ctx.options.lineWidth,
      minContentWidth: ctx.options.minContentWidth
    });
    var containsDocumentMarker = (str) => /^(%|---|\.\.\.)/m.test(str);
    function lineLengthOverLimit(str, lineWidth, indentLength) {
      if (!lineWidth || lineWidth < 0)
        return false;
      const limit = lineWidth - indentLength;
      const strLen = str.length;
      if (strLen <= limit)
        return false;
      for (let i = 0, start = 0; i < strLen; ++i) {
        if (str[i] === "\n") {
          if (i - start > limit)
            return true;
          start = i + 1;
          if (strLen - start <= limit)
            return false;
        }
      }
      return true;
    }
    function doubleQuotedString(value, ctx) {
      const json7 = JSON.stringify(value);
      if (ctx.options.doubleQuotedAsJSON)
        return json7;
      const { implicitKey } = ctx;
      const minMultiLineLength = ctx.options.doubleQuotedMinMultiLineLength;
      const indent = ctx.indent || (containsDocumentMarker(value) ? "  " : "");
      let str = "";
      let start = 0;
      for (let i = 0, ch = json7[i]; ch; ch = json7[++i]) {
        if (ch === " " && json7[i + 1] === "\\" && json7[i + 2] === "n") {
          str += json7.slice(start, i) + "\\ ";
          i += 1;
          start = i;
          ch = "\\";
        }
        if (ch === "\\")
          switch (json7[i + 1]) {
            case "u":
              {
                str += json7.slice(start, i);
                const code = json7.substr(i + 2, 4);
                switch (code) {
                  case "0000":
                    str += "\\0";
                    break;
                  case "0007":
                    str += "\\a";
                    break;
                  case "000b":
                    str += "\\v";
                    break;
                  case "001b":
                    str += "\\e";
                    break;
                  case "0085":
                    str += "\\N";
                    break;
                  case "00a0":
                    str += "\\_";
                    break;
                  case "2028":
                    str += "\\L";
                    break;
                  case "2029":
                    str += "\\P";
                    break;
                  default:
                    if (code.substr(0, 2) === "00")
                      str += "\\x" + code.substr(2);
                    else
                      str += json7.substr(i, 6);
                }
                i += 5;
                start = i + 1;
              }
              break;
            case "n":
              if (implicitKey || json7[i + 2] === '"' || json7.length < minMultiLineLength) {
                i += 1;
              } else {
                str += json7.slice(start, i) + "\n\n";
                while (json7[i + 2] === "\\" && json7[i + 3] === "n" && json7[i + 4] !== '"') {
                  str += "\n";
                  i += 2;
                }
                str += indent;
                if (json7[i + 2] === " ")
                  str += "\\";
                i += 1;
                start = i + 1;
              }
              break;
            default:
              i += 1;
          }
      }
      str = start ? str + json7.slice(start) : json7;
      return implicitKey ? str : foldFlowLines.foldFlowLines(str, indent, foldFlowLines.FOLD_QUOTED, getFoldOptions(ctx, false));
    }
    function singleQuotedString(value, ctx) {
      if (ctx.options.singleQuote === false || ctx.implicitKey && value.includes("\n") || /[ \t]\n|\n[ \t]/.test(value))
        return doubleQuotedString(value, ctx);
      const indent = ctx.indent || (containsDocumentMarker(value) ? "  " : "");
      const res = "'" + value.replace(/'/g, "''").replace(/\n+/g, `$&
${indent}`) + "'";
      return ctx.implicitKey ? res : foldFlowLines.foldFlowLines(res, indent, foldFlowLines.FOLD_FLOW, getFoldOptions(ctx, false));
    }
    function quotedString(value, ctx) {
      const { singleQuote } = ctx.options;
      let qs;
      if (singleQuote === false)
        qs = doubleQuotedString;
      else {
        const hasDouble = value.includes('"');
        const hasSingle = value.includes("'");
        if (hasDouble && !hasSingle)
          qs = singleQuotedString;
        else if (hasSingle && !hasDouble)
          qs = doubleQuotedString;
        else
          qs = singleQuote ? singleQuotedString : doubleQuotedString;
      }
      return qs(value, ctx);
    }
    var blockEndNewlines;
    try {
      blockEndNewlines = new RegExp("(^|(?<!\n))\n+(?!\n|$)", "g");
    } catch {
      blockEndNewlines = /\n+(?!\n|$)/g;
    }
    function blockString({ comment, type, value }, ctx, onComment, onChompKeep) {
      const { blockQuote, commentString, lineWidth } = ctx.options;
      if (!blockQuote || /\n[\t ]+$/.test(value)) {
        return quotedString(value, ctx);
      }
      const indent = ctx.indent || (ctx.forceBlockIndent || containsDocumentMarker(value) ? "  " : "");
      const literal = blockQuote === "literal" ? true : blockQuote === "folded" || type === Scalar.Scalar.BLOCK_FOLDED ? false : type === Scalar.Scalar.BLOCK_LITERAL ? true : !lineLengthOverLimit(value, lineWidth, indent.length);
      if (!value)
        return literal ? "|\n" : ">\n";
      let chomp;
      let endStart;
      for (endStart = value.length; endStart > 0; --endStart) {
        const ch = value[endStart - 1];
        if (ch !== "\n" && ch !== "	" && ch !== " ")
          break;
      }
      let end = value.substring(endStart);
      const endNlPos = end.indexOf("\n");
      if (endNlPos === -1) {
        chomp = "-";
      } else if (value === end || endNlPos !== end.length - 1) {
        chomp = "+";
        if (onChompKeep)
          onChompKeep();
      } else {
        chomp = "";
      }
      if (end) {
        value = value.slice(0, -end.length);
        if (end[end.length - 1] === "\n")
          end = end.slice(0, -1);
        end = end.replace(blockEndNewlines, `$&${indent}`);
      }
      let startWithSpace = false;
      let startEnd;
      let startNlPos = -1;
      for (startEnd = 0; startEnd < value.length; ++startEnd) {
        const ch = value[startEnd];
        if (ch === " ")
          startWithSpace = true;
        else if (ch === "\n")
          startNlPos = startEnd;
        else
          break;
      }
      let start = value.substring(0, startNlPos < startEnd ? startNlPos + 1 : startEnd);
      if (start) {
        value = value.substring(start.length);
        start = start.replace(/\n+/g, `$&${indent}`);
      }
      const indentSize = indent ? "2" : "1";
      let header = (startWithSpace ? indentSize : "") + chomp;
      if (comment) {
        header += " " + commentString(comment.replace(/ ?[\r\n]+/g, " "));
        if (onComment)
          onComment();
      }
      if (!literal) {
        const foldedValue = value.replace(/\n+/g, "\n$&").replace(/(?:^|\n)([\t ].*)(?:([\n\t ]*)\n(?![\n\t ]))?/g, "$1$2").replace(/\n+/g, `$&${indent}`);
        let literalFallback = false;
        const foldOptions = getFoldOptions(ctx, true);
        if (blockQuote !== "folded" && type !== Scalar.Scalar.BLOCK_FOLDED) {
          foldOptions.onOverflow = () => {
            literalFallback = true;
          };
        }
        const body = foldFlowLines.foldFlowLines(`${start}${foldedValue}${end}`, indent, foldFlowLines.FOLD_BLOCK, foldOptions);
        if (!literalFallback)
          return `>${header}
${indent}${body}`;
      }
      value = value.replace(/\n+/g, `$&${indent}`);
      return `|${header}
${indent}${start}${value}${end}`;
    }
    function plainString(item, ctx, onComment, onChompKeep) {
      const { type, value } = item;
      const { actualString, implicitKey, indent, indentStep, inFlow } = ctx;
      if (implicitKey && value.includes("\n") || inFlow && /[[\]{},]/.test(value)) {
        return quotedString(value, ctx);
      }
      if (/^[\n\t ,[\]{}#&*!|>'"%@`]|^[?-]$|^[?-][ \t]|[\n:][ \t]|[ \t]\n|[\n\t ]#|[\n\t :]$/.test(value)) {
        return implicitKey || inFlow || !value.includes("\n") ? quotedString(value, ctx) : blockString(item, ctx, onComment, onChompKeep);
      }
      if (!implicitKey && !inFlow && type !== Scalar.Scalar.PLAIN && value.includes("\n")) {
        return blockString(item, ctx, onComment, onChompKeep);
      }
      if (containsDocumentMarker(value)) {
        if (indent === "") {
          ctx.forceBlockIndent = true;
          return blockString(item, ctx, onComment, onChompKeep);
        } else if (implicitKey && indent === indentStep) {
          return quotedString(value, ctx);
        }
      }
      const str = value.replace(/\n+/g, `$&
${indent}`);
      if (actualString) {
        const test = (tag) => tag.default && tag.tag !== "tag:yaml.org,2002:str" && tag.test?.test(str);
        const { compat, tags } = ctx.doc.schema;
        if (tags.some(test) || compat?.some(test))
          return quotedString(value, ctx);
      }
      return implicitKey ? str : foldFlowLines.foldFlowLines(str, indent, foldFlowLines.FOLD_FLOW, getFoldOptions(ctx, false));
    }
    function stringifyString(item, ctx, onComment, onChompKeep) {
      const { implicitKey, inFlow } = ctx;
      const ss = typeof item.value === "string" ? item : Object.assign({}, item, { value: String(item.value) });
      let { type } = item;
      if (type !== Scalar.Scalar.QUOTE_DOUBLE) {
        if (/[\x00-\x08\x0b-\x1f\x7f-\x9f\u{D800}-\u{DFFF}]/u.test(ss.value))
          type = Scalar.Scalar.QUOTE_DOUBLE;
      }
      const _stringify = (_type) => {
        switch (_type) {
          case Scalar.Scalar.BLOCK_FOLDED:
          case Scalar.Scalar.BLOCK_LITERAL:
            return implicitKey || inFlow ? quotedString(ss.value, ctx) : blockString(ss, ctx, onComment, onChompKeep);
          case Scalar.Scalar.QUOTE_DOUBLE:
            return doubleQuotedString(ss.value, ctx);
          case Scalar.Scalar.QUOTE_SINGLE:
            return singleQuotedString(ss.value, ctx);
          case Scalar.Scalar.PLAIN:
            return plainString(ss, ctx, onComment, onChompKeep);
          default:
            return null;
        }
      };
      let res = _stringify(type);
      if (res === null) {
        const { defaultKeyType, defaultStringType } = ctx.options;
        const t = implicitKey && defaultKeyType || defaultStringType;
        res = _stringify(t);
        if (res === null)
          throw new Error(`Unsupported default string type ${t}`);
      }
      return res;
    }
    exports.stringifyString = stringifyString;
  }
});

// node_modules/yaml/dist/stringify/stringify.js
var require_stringify = __commonJS({
  "node_modules/yaml/dist/stringify/stringify.js"(exports) {
    "use strict";
    var anchors = require_anchors();
    var identity = require_identity();
    var stringifyComment = require_stringifyComment();
    var stringifyString = require_stringifyString();
    function createStringifyContext(doc, options) {
      const opt = Object.assign({
        blockQuote: true,
        commentString: stringifyComment.stringifyComment,
        defaultKeyType: null,
        defaultStringType: "PLAIN",
        directives: null,
        doubleQuotedAsJSON: false,
        doubleQuotedMinMultiLineLength: 40,
        falseStr: "false",
        flowCollectionPadding: true,
        indentSeq: true,
        lineWidth: 80,
        minContentWidth: 20,
        nullStr: "null",
        simpleKeys: false,
        singleQuote: null,
        trailingComma: false,
        trueStr: "true",
        verifyAliasOrder: true
      }, doc.schema.toStringOptions, options);
      let inFlow;
      switch (opt.collectionStyle) {
        case "block":
          inFlow = false;
          break;
        case "flow":
          inFlow = true;
          break;
        default:
          inFlow = null;
      }
      return {
        anchors: /* @__PURE__ */ new Set(),
        doc,
        flowCollectionPadding: opt.flowCollectionPadding ? " " : "",
        indent: "",
        indentStep: typeof opt.indent === "number" ? " ".repeat(opt.indent) : "  ",
        inFlow,
        options: opt
      };
    }
    function getTagObject(tags, item) {
      if (item.tag) {
        const match = tags.filter((t) => t.tag === item.tag);
        if (match.length > 0)
          return match.find((t) => t.format === item.format) ?? match[0];
      }
      let tagObj = void 0;
      let obj;
      if (identity.isScalar(item)) {
        obj = item.value;
        let match = tags.filter((t) => t.identify?.(obj));
        if (match.length > 1) {
          const testMatch = match.filter((t) => t.test);
          if (testMatch.length > 0)
            match = testMatch;
        }
        tagObj = match.find((t) => t.format === item.format) ?? match.find((t) => !t.format);
      } else {
        obj = item;
        tagObj = tags.find((t) => t.nodeClass && obj instanceof t.nodeClass);
      }
      if (!tagObj) {
        const name = obj?.constructor?.name ?? (obj === null ? "null" : typeof obj);
        throw new Error(`Tag not resolved for ${name} value`);
      }
      return tagObj;
    }
    function stringifyProps(node, tagObj, { anchors: anchors$1, doc }) {
      if (!doc.directives)
        return "";
      const props = [];
      const anchor = (identity.isScalar(node) || identity.isCollection(node)) && node.anchor;
      if (anchor && anchors.anchorIsValid(anchor)) {
        anchors$1.add(anchor);
        props.push(`&${anchor}`);
      }
      const tag = node.tag ?? (tagObj.default ? null : tagObj.tag);
      if (tag)
        props.push(doc.directives.tagString(tag));
      return props.join(" ");
    }
    function stringify(item, ctx, onComment, onChompKeep) {
      if (identity.isPair(item))
        return item.toString(ctx, onComment, onChompKeep);
      if (identity.isAlias(item)) {
        if (ctx.doc.directives)
          return item.toString(ctx);
        if (ctx.resolvedAliases?.has(item)) {
          throw new TypeError(`Cannot stringify circular structure without alias nodes`);
        } else {
          if (ctx.resolvedAliases)
            ctx.resolvedAliases.add(item);
          else
            ctx.resolvedAliases = /* @__PURE__ */ new Set([item]);
          item = item.resolve(ctx.doc);
        }
      }
      let tagObj = void 0;
      const node = identity.isNode(item) ? item : ctx.doc.createNode(item, { onTagObj: (o) => tagObj = o });
      tagObj ?? (tagObj = getTagObject(ctx.doc.schema.tags, node));
      const props = stringifyProps(node, tagObj, ctx);
      if (props.length > 0)
        ctx.indentAtStart = (ctx.indentAtStart ?? 0) + props.length + 1;
      const str = typeof tagObj.stringify === "function" ? tagObj.stringify(node, ctx, onComment, onChompKeep) : identity.isScalar(node) ? stringifyString.stringifyString(node, ctx, onComment, onChompKeep) : node.toString(ctx, onComment, onChompKeep);
      if (!props)
        return str;
      return identity.isScalar(node) || str[0] === "{" || str[0] === "[" ? `${props} ${str}` : `${props}
${ctx.indent}${str}`;
    }
    exports.createStringifyContext = createStringifyContext;
    exports.stringify = stringify;
  }
});

// node_modules/yaml/dist/stringify/stringifyPair.js
var require_stringifyPair = __commonJS({
  "node_modules/yaml/dist/stringify/stringifyPair.js"(exports) {
    "use strict";
    var identity = require_identity();
    var Scalar = require_Scalar();
    var stringify = require_stringify();
    var stringifyComment = require_stringifyComment();
    function stringifyPair({ key, value }, ctx, onComment, onChompKeep) {
      const { allNullValues, doc, indent, indentStep, options: { commentString, indentSeq, simpleKeys } } = ctx;
      let keyComment = identity.isNode(key) && key.comment || null;
      if (simpleKeys) {
        if (keyComment) {
          throw new Error("With simple keys, key nodes cannot have comments");
        }
        if (identity.isCollection(key) || !identity.isNode(key) && typeof key === "object") {
          const msg = "With simple keys, collection cannot be used as a key value";
          throw new Error(msg);
        }
      }
      let explicitKey = !simpleKeys && (!key || keyComment && value == null && !ctx.inFlow || identity.isCollection(key) || (identity.isScalar(key) ? key.type === Scalar.Scalar.BLOCK_FOLDED || key.type === Scalar.Scalar.BLOCK_LITERAL : typeof key === "object"));
      ctx = Object.assign({}, ctx, {
        allNullValues: false,
        implicitKey: !explicitKey && (simpleKeys || !allNullValues),
        indent: indent + indentStep
      });
      let keyCommentDone = false;
      let chompKeep = false;
      let str = stringify.stringify(key, ctx, () => keyCommentDone = true, () => chompKeep = true);
      if (!explicitKey && !ctx.inFlow && str.length > 1024) {
        if (simpleKeys)
          throw new Error("With simple keys, single line scalar must not span more than 1024 characters");
        explicitKey = true;
      }
      if (ctx.inFlow) {
        if (allNullValues || value == null) {
          if (keyCommentDone && onComment)
            onComment();
          return str === "" ? "?" : explicitKey ? `? ${str}` : str;
        }
      } else if (allNullValues && !simpleKeys || value == null && explicitKey) {
        str = `? ${str}`;
        if (keyComment && !keyCommentDone) {
          str += stringifyComment.lineComment(str, ctx.indent, commentString(keyComment));
        } else if (chompKeep && onChompKeep)
          onChompKeep();
        return str;
      }
      if (keyCommentDone)
        keyComment = null;
      if (explicitKey) {
        if (keyComment)
          str += stringifyComment.lineComment(str, ctx.indent, commentString(keyComment));
        str = `? ${str}
${indent}:`;
      } else {
        str = `${str}:`;
        if (keyComment)
          str += stringifyComment.lineComment(str, ctx.indent, commentString(keyComment));
      }
      let vsb, vcb, valueComment;
      if (identity.isNode(value)) {
        vsb = !!value.spaceBefore;
        vcb = value.commentBefore;
        valueComment = value.comment;
      } else {
        vsb = false;
        vcb = null;
        valueComment = null;
        if (value && typeof value === "object")
          value = doc.createNode(value);
      }
      ctx.implicitKey = false;
      if (!explicitKey && !keyComment && identity.isScalar(value))
        ctx.indentAtStart = str.length + 1;
      chompKeep = false;
      if (!indentSeq && indentStep.length >= 2 && !ctx.inFlow && !explicitKey && identity.isSeq(value) && !value.flow && !value.tag && !value.anchor) {
        ctx.indent = ctx.indent.substring(2);
      }
      let valueCommentDone = false;
      const valueStr = stringify.stringify(value, ctx, () => valueCommentDone = true, () => chompKeep = true);
      let ws = " ";
      if (keyComment || vsb || vcb) {
        ws = vsb ? "\n" : "";
        if (vcb) {
          const cs = commentString(vcb);
          ws += `
${stringifyComment.indentComment(cs, ctx.indent)}`;
        }
        if (valueStr === "" && !ctx.inFlow) {
          if (ws === "\n" && valueComment)
            ws = "\n\n";
        } else {
          ws += `
${ctx.indent}`;
        }
      } else if (!explicitKey && identity.isCollection(value)) {
        const vs0 = valueStr[0];
        const nl0 = valueStr.indexOf("\n");
        const hasNewline = nl0 !== -1;
        const flow = ctx.inFlow ?? value.flow ?? value.items.length === 0;
        if (hasNewline || !flow) {
          let hasPropsLine = false;
          if (hasNewline && (vs0 === "&" || vs0 === "!")) {
            let sp0 = valueStr.indexOf(" ");
            if (vs0 === "&" && sp0 !== -1 && sp0 < nl0 && valueStr[sp0 + 1] === "!") {
              sp0 = valueStr.indexOf(" ", sp0 + 1);
            }
            if (sp0 === -1 || nl0 < sp0)
              hasPropsLine = true;
          }
          if (!hasPropsLine)
            ws = `
${ctx.indent}`;
        }
      } else if (valueStr === "" || valueStr[0] === "\n") {
        ws = "";
      }
      str += ws + valueStr;
      if (ctx.inFlow) {
        if (valueCommentDone && onComment)
          onComment();
      } else if (valueComment && !valueCommentDone) {
        str += stringifyComment.lineComment(str, ctx.indent, commentString(valueComment));
      } else if (chompKeep && onChompKeep) {
        onChompKeep();
      }
      return str;
    }
    exports.stringifyPair = stringifyPair;
  }
});

// node_modules/yaml/dist/log.js
var require_log = __commonJS({
  "node_modules/yaml/dist/log.js"(exports) {
    "use strict";
    var node_process = __require("process");
    function debug(logLevel, ...messages) {
      if (logLevel === "debug")
        console.log(...messages);
    }
    function warn(logLevel, warning) {
      if (logLevel === "debug" || logLevel === "warn") {
        if (typeof node_process.emitWarning === "function")
          node_process.emitWarning(warning);
        else
          console.warn(warning);
      }
    }
    exports.debug = debug;
    exports.warn = warn;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/merge.js
var require_merge = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/merge.js"(exports) {
    "use strict";
    var identity = require_identity();
    var Scalar = require_Scalar();
    var MERGE_KEY = "<<";
    var merge = {
      identify: (value) => value === MERGE_KEY || typeof value === "symbol" && value.description === MERGE_KEY,
      default: "key",
      tag: "tag:yaml.org,2002:merge",
      test: /^<<$/,
      resolve: () => Object.assign(new Scalar.Scalar(Symbol(MERGE_KEY)), {
        addToJSMap: addMergeToJSMap
      }),
      stringify: () => MERGE_KEY
    };
    var isMergeKey = (ctx, key) => (merge.identify(key) || identity.isScalar(key) && (!key.type || key.type === Scalar.Scalar.PLAIN) && merge.identify(key.value)) && ctx?.doc.schema.tags.some((tag) => tag.tag === merge.tag && tag.default);
    function addMergeToJSMap(ctx, map, value) {
      const source = resolveAliasValue(ctx, value);
      if (identity.isSeq(source))
        for (const it of source.items)
          mergeValue(ctx, map, it);
      else if (Array.isArray(source))
        for (const it of source)
          mergeValue(ctx, map, it);
      else
        mergeValue(ctx, map, source);
    }
    function mergeValue(ctx, map, value) {
      const source = resolveAliasValue(ctx, value);
      if (!identity.isMap(source))
        throw new Error("Merge sources must be maps or map aliases");
      const srcMap = source.toJSON(null, ctx, Map);
      for (const [key, value2] of srcMap) {
        if (map instanceof Map) {
          if (!map.has(key))
            map.set(key, value2);
        } else if (map instanceof Set) {
          map.add(key);
        } else if (!Object.prototype.hasOwnProperty.call(map, key)) {
          Object.defineProperty(map, key, {
            value: value2,
            writable: true,
            enumerable: true,
            configurable: true
          });
        }
      }
      return map;
    }
    function resolveAliasValue(ctx, value) {
      return ctx && identity.isAlias(value) ? value.resolve(ctx.doc, ctx) : value;
    }
    exports.addMergeToJSMap = addMergeToJSMap;
    exports.isMergeKey = isMergeKey;
    exports.merge = merge;
  }
});

// node_modules/yaml/dist/nodes/addPairToJSMap.js
var require_addPairToJSMap = __commonJS({
  "node_modules/yaml/dist/nodes/addPairToJSMap.js"(exports) {
    "use strict";
    var log = require_log();
    var merge = require_merge();
    var stringify = require_stringify();
    var identity = require_identity();
    var toJS = require_toJS();
    function addPairToJSMap(ctx, map, { key, value }) {
      if (identity.isNode(key) && key.addToJSMap)
        key.addToJSMap(ctx, map, value);
      else if (merge.isMergeKey(ctx, key))
        merge.addMergeToJSMap(ctx, map, value);
      else {
        const jsKey = toJS.toJS(key, "", ctx);
        if (map instanceof Map) {
          map.set(jsKey, toJS.toJS(value, jsKey, ctx));
        } else if (map instanceof Set) {
          map.add(jsKey);
        } else {
          const stringKey = stringifyKey(key, jsKey, ctx);
          const jsValue = toJS.toJS(value, stringKey, ctx);
          if (stringKey in map)
            Object.defineProperty(map, stringKey, {
              value: jsValue,
              writable: true,
              enumerable: true,
              configurable: true
            });
          else
            map[stringKey] = jsValue;
        }
      }
      return map;
    }
    function stringifyKey(key, jsKey, ctx) {
      if (jsKey === null)
        return "";
      if (typeof jsKey !== "object")
        return String(jsKey);
      if (identity.isNode(key) && ctx?.doc) {
        const strCtx = stringify.createStringifyContext(ctx.doc, {});
        strCtx.anchors = /* @__PURE__ */ new Set();
        for (const node of ctx.anchors.keys())
          strCtx.anchors.add(node.anchor);
        strCtx.inFlow = true;
        strCtx.inStringifyKey = true;
        const strKey = key.toString(strCtx);
        if (!ctx.mapKeyWarned) {
          let jsonStr = JSON.stringify(strKey);
          if (jsonStr.length > 40)
            jsonStr = jsonStr.substring(0, 36) + '..."';
          log.warn(ctx.doc.options.logLevel, `Keys with collection values will be stringified due to JS Object restrictions: ${jsonStr}. Set mapAsMap: true to use object keys.`);
          ctx.mapKeyWarned = true;
        }
        return strKey;
      }
      return JSON.stringify(jsKey);
    }
    exports.addPairToJSMap = addPairToJSMap;
  }
});

// node_modules/yaml/dist/nodes/Pair.js
var require_Pair = __commonJS({
  "node_modules/yaml/dist/nodes/Pair.js"(exports) {
    "use strict";
    var createNode = require_createNode();
    var stringifyPair = require_stringifyPair();
    var addPairToJSMap = require_addPairToJSMap();
    var identity = require_identity();
    function createPair(key, value, ctx) {
      const k = createNode.createNode(key, void 0, ctx);
      const v = createNode.createNode(value, void 0, ctx);
      return new Pair(k, v);
    }
    var Pair = class _Pair {
      constructor(key, value = null) {
        Object.defineProperty(this, identity.NODE_TYPE, { value: identity.PAIR });
        this.key = key;
        this.value = value;
      }
      clone(schema) {
        let { key, value } = this;
        if (identity.isNode(key))
          key = key.clone(schema);
        if (identity.isNode(value))
          value = value.clone(schema);
        return new _Pair(key, value);
      }
      toJSON(_, ctx) {
        const pair = ctx?.mapAsMap ? /* @__PURE__ */ new Map() : {};
        return addPairToJSMap.addPairToJSMap(ctx, pair, this);
      }
      toString(ctx, onComment, onChompKeep) {
        return ctx?.doc ? stringifyPair.stringifyPair(this, ctx, onComment, onChompKeep) : JSON.stringify(this);
      }
    };
    exports.Pair = Pair;
    exports.createPair = createPair;
  }
});

// node_modules/yaml/dist/stringify/stringifyCollection.js
var require_stringifyCollection = __commonJS({
  "node_modules/yaml/dist/stringify/stringifyCollection.js"(exports) {
    "use strict";
    var identity = require_identity();
    var stringify = require_stringify();
    var stringifyComment = require_stringifyComment();
    function stringifyCollection(collection, ctx, options) {
      const flow = ctx.inFlow ?? collection.flow;
      const stringify2 = flow ? stringifyFlowCollection : stringifyBlockCollection;
      return stringify2(collection, ctx, options);
    }
    function stringifyBlockCollection({ comment, items }, ctx, { blockItemPrefix, flowChars, itemIndent, onChompKeep, onComment }) {
      const { indent, options: { commentString } } = ctx;
      const itemCtx = Object.assign({}, ctx, { indent: itemIndent, type: null });
      let chompKeep = false;
      const lines = [];
      for (let i = 0; i < items.length; ++i) {
        const item = items[i];
        let comment2 = null;
        if (identity.isNode(item)) {
          if (!chompKeep && item.spaceBefore)
            lines.push("");
          addCommentBefore(ctx, lines, item.commentBefore, chompKeep);
          if (item.comment)
            comment2 = item.comment;
        } else if (identity.isPair(item)) {
          const ik = identity.isNode(item.key) ? item.key : null;
          if (ik) {
            if (!chompKeep && ik.spaceBefore)
              lines.push("");
            addCommentBefore(ctx, lines, ik.commentBefore, chompKeep);
          }
        }
        chompKeep = false;
        let str2 = stringify.stringify(item, itemCtx, () => comment2 = null, () => chompKeep = true);
        if (comment2)
          str2 += stringifyComment.lineComment(str2, itemIndent, commentString(comment2));
        if (chompKeep && comment2)
          chompKeep = false;
        lines.push(blockItemPrefix + str2);
      }
      let str;
      if (lines.length === 0) {
        str = flowChars.start + flowChars.end;
      } else {
        str = lines[0];
        for (let i = 1; i < lines.length; ++i) {
          const line = lines[i];
          str += line ? `
${indent}${line}` : "\n";
        }
      }
      if (comment) {
        str += "\n" + stringifyComment.indentComment(commentString(comment), indent);
        if (onComment)
          onComment();
      } else if (chompKeep && onChompKeep)
        onChompKeep();
      return str;
    }
    function stringifyFlowCollection({ items }, ctx, { flowChars, itemIndent }) {
      const { indent, indentStep, flowCollectionPadding: fcPadding, options: { commentString } } = ctx;
      itemIndent += indentStep;
      const itemCtx = Object.assign({}, ctx, {
        indent: itemIndent,
        inFlow: true,
        type: null
      });
      let reqNewline = false;
      let linesAtValue = 0;
      const lines = [];
      for (let i = 0; i < items.length; ++i) {
        const item = items[i];
        let comment = null;
        if (identity.isNode(item)) {
          if (item.spaceBefore)
            lines.push("");
          addCommentBefore(ctx, lines, item.commentBefore, false);
          if (item.comment)
            comment = item.comment;
        } else if (identity.isPair(item)) {
          const ik = identity.isNode(item.key) ? item.key : null;
          if (ik) {
            if (ik.spaceBefore)
              lines.push("");
            addCommentBefore(ctx, lines, ik.commentBefore, false);
            if (ik.comment)
              reqNewline = true;
          }
          const iv = identity.isNode(item.value) ? item.value : null;
          if (iv) {
            if (iv.comment)
              comment = iv.comment;
            if (iv.commentBefore)
              reqNewline = true;
          } else if (item.value == null && ik?.comment) {
            comment = ik.comment;
          }
        }
        if (comment)
          reqNewline = true;
        let str = stringify.stringify(item, itemCtx, () => comment = null);
        reqNewline || (reqNewline = lines.length > linesAtValue || str.includes("\n"));
        if (i < items.length - 1) {
          str += ",";
        } else if (ctx.options.trailingComma) {
          if (ctx.options.lineWidth > 0) {
            reqNewline || (reqNewline = lines.reduce((sum, line) => sum + line.length + 2, 2) + (str.length + 2) > ctx.options.lineWidth);
          }
          if (reqNewline) {
            str += ",";
          }
        }
        if (comment)
          str += stringifyComment.lineComment(str, itemIndent, commentString(comment));
        lines.push(str);
        linesAtValue = lines.length;
      }
      const { start, end } = flowChars;
      if (lines.length === 0) {
        return start + end;
      } else {
        if (!reqNewline) {
          const len = lines.reduce((sum, line) => sum + line.length + 2, 2);
          reqNewline = ctx.options.lineWidth > 0 && len > ctx.options.lineWidth;
        }
        if (reqNewline) {
          let str = start;
          for (const line of lines)
            str += line ? `
${indentStep}${indent}${line}` : "\n";
          return `${str}
${indent}${end}`;
        } else {
          return `${start}${fcPadding}${lines.join(" ")}${fcPadding}${end}`;
        }
      }
    }
    function addCommentBefore({ indent, options: { commentString } }, lines, comment, chompKeep) {
      if (comment && chompKeep)
        comment = comment.replace(/^\n+/, "");
      if (comment) {
        const ic = stringifyComment.indentComment(commentString(comment), indent);
        lines.push(ic.trimStart());
      }
    }
    exports.stringifyCollection = stringifyCollection;
  }
});

// node_modules/yaml/dist/nodes/YAMLMap.js
var require_YAMLMap = __commonJS({
  "node_modules/yaml/dist/nodes/YAMLMap.js"(exports) {
    "use strict";
    var stringifyCollection = require_stringifyCollection();
    var addPairToJSMap = require_addPairToJSMap();
    var Collection = require_Collection();
    var identity = require_identity();
    var Pair = require_Pair();
    var Scalar = require_Scalar();
    function findPair(items, key) {
      const k = identity.isScalar(key) ? key.value : key;
      for (const it of items) {
        if (identity.isPair(it)) {
          if (it.key === key || it.key === k)
            return it;
          if (identity.isScalar(it.key) && it.key.value === k)
            return it;
        }
      }
      return void 0;
    }
    var YAMLMap = class extends Collection.Collection {
      static get tagName() {
        return "tag:yaml.org,2002:map";
      }
      constructor(schema) {
        super(identity.MAP, schema);
        this.items = [];
      }
      /**
       * A generic collection parsing method that can be extended
       * to other node classes that inherit from YAMLMap
       */
      static from(schema, obj, ctx) {
        const { keepUndefined, replacer } = ctx;
        const map = new this(schema);
        const add = (key, value) => {
          if (typeof replacer === "function")
            value = replacer.call(obj, key, value);
          else if (Array.isArray(replacer) && !replacer.includes(key))
            return;
          if (value !== void 0 || keepUndefined)
            map.items.push(Pair.createPair(key, value, ctx));
        };
        if (obj instanceof Map) {
          for (const [key, value] of obj)
            add(key, value);
        } else if (obj && typeof obj === "object") {
          for (const key of Object.keys(obj))
            add(key, obj[key]);
        }
        if (typeof schema.sortMapEntries === "function") {
          map.items.sort(schema.sortMapEntries);
        }
        return map;
      }
      /**
       * Adds a value to the collection.
       *
       * @param overwrite - If not set `true`, using a key that is already in the
       *   collection will throw. Otherwise, overwrites the previous value.
       */
      add(pair, overwrite) {
        let _pair;
        if (identity.isPair(pair))
          _pair = pair;
        else if (!pair || typeof pair !== "object" || !("key" in pair)) {
          _pair = new Pair.Pair(pair, pair?.value);
        } else
          _pair = new Pair.Pair(pair.key, pair.value);
        const prev = findPair(this.items, _pair.key);
        const sortEntries = this.schema?.sortMapEntries;
        if (prev) {
          if (!overwrite)
            throw new Error(`Key ${_pair.key} already set`);
          if (identity.isScalar(prev.value) && Scalar.isScalarValue(_pair.value))
            prev.value.value = _pair.value;
          else
            prev.value = _pair.value;
        } else if (sortEntries) {
          const i = this.items.findIndex((item) => sortEntries(_pair, item) < 0);
          if (i === -1)
            this.items.push(_pair);
          else
            this.items.splice(i, 0, _pair);
        } else {
          this.items.push(_pair);
        }
      }
      delete(key) {
        const it = findPair(this.items, key);
        if (!it)
          return false;
        const del = this.items.splice(this.items.indexOf(it), 1);
        return del.length > 0;
      }
      get(key, keepScalar) {
        const it = findPair(this.items, key);
        const node = it?.value;
        return (!keepScalar && identity.isScalar(node) ? node.value : node) ?? void 0;
      }
      has(key) {
        return !!findPair(this.items, key);
      }
      set(key, value) {
        this.add(new Pair.Pair(key, value), true);
      }
      /**
       * @param ctx - Conversion context, originally set in Document#toJS()
       * @param {Class} Type - If set, forces the returned collection type
       * @returns Instance of Type, Map, or Object
       */
      toJSON(_, ctx, Type) {
        const map = Type ? new Type() : ctx?.mapAsMap ? /* @__PURE__ */ new Map() : {};
        if (ctx?.onCreate)
          ctx.onCreate(map);
        for (const item of this.items)
          addPairToJSMap.addPairToJSMap(ctx, map, item);
        return map;
      }
      toString(ctx, onComment, onChompKeep) {
        if (!ctx)
          return JSON.stringify(this);
        for (const item of this.items) {
          if (!identity.isPair(item))
            throw new Error(`Map items must all be pairs; found ${JSON.stringify(item)} instead`);
        }
        if (!ctx.allNullValues && this.hasAllNullValues(false))
          ctx = Object.assign({}, ctx, { allNullValues: true });
        return stringifyCollection.stringifyCollection(this, ctx, {
          blockItemPrefix: "",
          flowChars: { start: "{", end: "}" },
          itemIndent: ctx.indent || "",
          onChompKeep,
          onComment
        });
      }
    };
    exports.YAMLMap = YAMLMap;
    exports.findPair = findPair;
  }
});

// node_modules/yaml/dist/schema/common/map.js
var require_map = __commonJS({
  "node_modules/yaml/dist/schema/common/map.js"(exports) {
    "use strict";
    var identity = require_identity();
    var YAMLMap = require_YAMLMap();
    var map = {
      collection: "map",
      default: true,
      nodeClass: YAMLMap.YAMLMap,
      tag: "tag:yaml.org,2002:map",
      resolve(map2, onError) {
        if (!identity.isMap(map2))
          onError("Expected a mapping for this tag");
        return map2;
      },
      createNode: (schema, obj, ctx) => YAMLMap.YAMLMap.from(schema, obj, ctx)
    };
    exports.map = map;
  }
});

// node_modules/yaml/dist/nodes/YAMLSeq.js
var require_YAMLSeq = __commonJS({
  "node_modules/yaml/dist/nodes/YAMLSeq.js"(exports) {
    "use strict";
    var createNode = require_createNode();
    var stringifyCollection = require_stringifyCollection();
    var Collection = require_Collection();
    var identity = require_identity();
    var Scalar = require_Scalar();
    var toJS = require_toJS();
    var YAMLSeq = class extends Collection.Collection {
      static get tagName() {
        return "tag:yaml.org,2002:seq";
      }
      constructor(schema) {
        super(identity.SEQ, schema);
        this.items = [];
      }
      add(value) {
        this.items.push(value);
      }
      /**
       * Removes a value from the collection.
       *
       * `key` must contain a representation of an integer for this to succeed.
       * It may be wrapped in a `Scalar`.
       *
       * @returns `true` if the item was found and removed.
       */
      delete(key) {
        const idx = asItemIndex(key);
        if (typeof idx !== "number")
          return false;
        const del = this.items.splice(idx, 1);
        return del.length > 0;
      }
      get(key, keepScalar) {
        const idx = asItemIndex(key);
        if (typeof idx !== "number")
          return void 0;
        const it = this.items[idx];
        return !keepScalar && identity.isScalar(it) ? it.value : it;
      }
      /**
       * Checks if the collection includes a value with the key `key`.
       *
       * `key` must contain a representation of an integer for this to succeed.
       * It may be wrapped in a `Scalar`.
       */
      has(key) {
        const idx = asItemIndex(key);
        return typeof idx === "number" && idx < this.items.length;
      }
      /**
       * Sets a value in this collection. For `!!set`, `value` needs to be a
       * boolean to add/remove the item from the set.
       *
       * If `key` does not contain a representation of an integer, this will throw.
       * It may be wrapped in a `Scalar`.
       */
      set(key, value) {
        const idx = asItemIndex(key);
        if (typeof idx !== "number")
          throw new Error(`Expected a valid index, not ${key}.`);
        const prev = this.items[idx];
        if (identity.isScalar(prev) && Scalar.isScalarValue(value))
          prev.value = value;
        else
          this.items[idx] = value;
      }
      toJSON(_, ctx) {
        const seq = [];
        if (ctx?.onCreate)
          ctx.onCreate(seq);
        let i = 0;
        for (const item of this.items)
          seq.push(toJS.toJS(item, String(i++), ctx));
        return seq;
      }
      toString(ctx, onComment, onChompKeep) {
        if (!ctx)
          return JSON.stringify(this);
        return stringifyCollection.stringifyCollection(this, ctx, {
          blockItemPrefix: "- ",
          flowChars: { start: "[", end: "]" },
          itemIndent: (ctx.indent || "") + "  ",
          onChompKeep,
          onComment
        });
      }
      static from(schema, obj, ctx) {
        const { replacer } = ctx;
        const seq = new this(schema);
        if (obj && Symbol.iterator in Object(obj)) {
          let i = 0;
          for (let it of obj) {
            if (typeof replacer === "function") {
              const key = obj instanceof Set ? it : String(i++);
              it = replacer.call(obj, key, it);
            }
            seq.items.push(createNode.createNode(it, void 0, ctx));
          }
        }
        return seq;
      }
    };
    function asItemIndex(key) {
      let idx = identity.isScalar(key) ? key.value : key;
      if (idx && typeof idx === "string")
        idx = Number(idx);
      return typeof idx === "number" && Number.isInteger(idx) && idx >= 0 ? idx : null;
    }
    exports.YAMLSeq = YAMLSeq;
  }
});

// node_modules/yaml/dist/schema/common/seq.js
var require_seq = __commonJS({
  "node_modules/yaml/dist/schema/common/seq.js"(exports) {
    "use strict";
    var identity = require_identity();
    var YAMLSeq = require_YAMLSeq();
    var seq = {
      collection: "seq",
      default: true,
      nodeClass: YAMLSeq.YAMLSeq,
      tag: "tag:yaml.org,2002:seq",
      resolve(seq2, onError) {
        if (!identity.isSeq(seq2))
          onError("Expected a sequence for this tag");
        return seq2;
      },
      createNode: (schema, obj, ctx) => YAMLSeq.YAMLSeq.from(schema, obj, ctx)
    };
    exports.seq = seq;
  }
});

// node_modules/yaml/dist/schema/common/string.js
var require_string = __commonJS({
  "node_modules/yaml/dist/schema/common/string.js"(exports) {
    "use strict";
    var stringifyString = require_stringifyString();
    var string = {
      identify: (value) => typeof value === "string",
      default: true,
      tag: "tag:yaml.org,2002:str",
      resolve: (str) => str,
      stringify(item, ctx, onComment, onChompKeep) {
        ctx = Object.assign({ actualString: true }, ctx);
        return stringifyString.stringifyString(item, ctx, onComment, onChompKeep);
      }
    };
    exports.string = string;
  }
});

// node_modules/yaml/dist/schema/common/null.js
var require_null = __commonJS({
  "node_modules/yaml/dist/schema/common/null.js"(exports) {
    "use strict";
    var Scalar = require_Scalar();
    var nullTag = {
      identify: (value) => value == null,
      createNode: () => new Scalar.Scalar(null),
      default: true,
      tag: "tag:yaml.org,2002:null",
      test: /^(?:~|[Nn]ull|NULL)?$/,
      resolve: () => new Scalar.Scalar(null),
      stringify: ({ source }, ctx) => typeof source === "string" && nullTag.test.test(source) ? source : ctx.options.nullStr
    };
    exports.nullTag = nullTag;
  }
});

// node_modules/yaml/dist/schema/core/bool.js
var require_bool = __commonJS({
  "node_modules/yaml/dist/schema/core/bool.js"(exports) {
    "use strict";
    var Scalar = require_Scalar();
    var boolTag = {
      identify: (value) => typeof value === "boolean",
      default: true,
      tag: "tag:yaml.org,2002:bool",
      test: /^(?:[Tt]rue|TRUE|[Ff]alse|FALSE)$/,
      resolve: (str) => new Scalar.Scalar(str[0] === "t" || str[0] === "T"),
      stringify({ source, value }, ctx) {
        if (source && boolTag.test.test(source)) {
          const sv = source[0] === "t" || source[0] === "T";
          if (value === sv)
            return source;
        }
        return value ? ctx.options.trueStr : ctx.options.falseStr;
      }
    };
    exports.boolTag = boolTag;
  }
});

// node_modules/yaml/dist/stringify/stringifyNumber.js
var require_stringifyNumber = __commonJS({
  "node_modules/yaml/dist/stringify/stringifyNumber.js"(exports) {
    "use strict";
    function stringifyNumber({ format, minFractionDigits, tag, value }) {
      if (typeof value === "bigint")
        return String(value);
      const num = typeof value === "number" ? value : Number(value);
      if (!isFinite(num))
        return isNaN(num) ? ".nan" : num < 0 ? "-.inf" : ".inf";
      let n = Object.is(value, -0) ? "-0" : JSON.stringify(value);
      if (!format && minFractionDigits && (!tag || tag === "tag:yaml.org,2002:float") && /^-?\d/.test(n) && !n.includes("e")) {
        let i = n.indexOf(".");
        if (i < 0) {
          i = n.length;
          n += ".";
        }
        let d = minFractionDigits - (n.length - i - 1);
        while (d-- > 0)
          n += "0";
      }
      return n;
    }
    exports.stringifyNumber = stringifyNumber;
  }
});

// node_modules/yaml/dist/schema/core/float.js
var require_float = __commonJS({
  "node_modules/yaml/dist/schema/core/float.js"(exports) {
    "use strict";
    var Scalar = require_Scalar();
    var stringifyNumber = require_stringifyNumber();
    var floatNaN = {
      identify: (value) => typeof value === "number",
      default: true,
      tag: "tag:yaml.org,2002:float",
      test: /^(?:[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)$/,
      resolve: (str) => str.slice(-3).toLowerCase() === "nan" ? NaN : str[0] === "-" ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY,
      stringify: stringifyNumber.stringifyNumber
    };
    var floatExp = {
      identify: (value) => typeof value === "number",
      default: true,
      tag: "tag:yaml.org,2002:float",
      format: "EXP",
      test: /^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)[eE][-+]?[0-9]+$/,
      resolve: (str) => parseFloat(str),
      stringify(node) {
        const num = Number(node.value);
        return isFinite(num) ? num.toExponential() : stringifyNumber.stringifyNumber(node);
      }
    };
    var float = {
      identify: (value) => typeof value === "number",
      default: true,
      tag: "tag:yaml.org,2002:float",
      test: /^[-+]?(?:\.[0-9]+|[0-9]+\.[0-9]*)$/,
      resolve(str) {
        const node = new Scalar.Scalar(parseFloat(str));
        const dot = str.indexOf(".");
        if (dot !== -1 && str[str.length - 1] === "0")
          node.minFractionDigits = str.length - dot - 1;
        return node;
      },
      stringify: stringifyNumber.stringifyNumber
    };
    exports.float = float;
    exports.floatExp = floatExp;
    exports.floatNaN = floatNaN;
  }
});

// node_modules/yaml/dist/schema/core/int.js
var require_int = __commonJS({
  "node_modules/yaml/dist/schema/core/int.js"(exports) {
    "use strict";
    var stringifyNumber = require_stringifyNumber();
    var intIdentify = (value) => typeof value === "bigint" || Number.isInteger(value);
    var intResolve = (str, offset, radix, { intAsBigInt }) => intAsBigInt ? BigInt(str) : parseInt(str.substring(offset), radix);
    function intStringify(node, radix, prefix) {
      const { value } = node;
      if (intIdentify(value) && value >= 0)
        return prefix + value.toString(radix);
      return stringifyNumber.stringifyNumber(node);
    }
    var intOct = {
      identify: (value) => intIdentify(value) && value >= 0,
      default: true,
      tag: "tag:yaml.org,2002:int",
      format: "OCT",
      test: /^0o[0-7]+$/,
      resolve: (str, _onError, opt) => intResolve(str, 2, 8, opt),
      stringify: (node) => intStringify(node, 8, "0o")
    };
    var int = {
      identify: intIdentify,
      default: true,
      tag: "tag:yaml.org,2002:int",
      test: /^[-+]?[0-9]+$/,
      resolve: (str, _onError, opt) => intResolve(str, 0, 10, opt),
      stringify: stringifyNumber.stringifyNumber
    };
    var intHex = {
      identify: (value) => intIdentify(value) && value >= 0,
      default: true,
      tag: "tag:yaml.org,2002:int",
      format: "HEX",
      test: /^0x[0-9a-fA-F]+$/,
      resolve: (str, _onError, opt) => intResolve(str, 2, 16, opt),
      stringify: (node) => intStringify(node, 16, "0x")
    };
    exports.int = int;
    exports.intHex = intHex;
    exports.intOct = intOct;
  }
});

// node_modules/yaml/dist/schema/core/schema.js
var require_schema = __commonJS({
  "node_modules/yaml/dist/schema/core/schema.js"(exports) {
    "use strict";
    var map = require_map();
    var _null = require_null();
    var seq = require_seq();
    var string = require_string();
    var bool = require_bool();
    var float = require_float();
    var int = require_int();
    var schema = [
      map.map,
      seq.seq,
      string.string,
      _null.nullTag,
      bool.boolTag,
      int.intOct,
      int.int,
      int.intHex,
      float.floatNaN,
      float.floatExp,
      float.float
    ];
    exports.schema = schema;
  }
});

// node_modules/yaml/dist/schema/json/schema.js
var require_schema2 = __commonJS({
  "node_modules/yaml/dist/schema/json/schema.js"(exports) {
    "use strict";
    var Scalar = require_Scalar();
    var map = require_map();
    var seq = require_seq();
    function intIdentify(value) {
      return typeof value === "bigint" || Number.isInteger(value);
    }
    var stringifyJSON = ({ value }) => JSON.stringify(value);
    var jsonScalars = [
      {
        identify: (value) => typeof value === "string",
        default: true,
        tag: "tag:yaml.org,2002:str",
        resolve: (str) => str,
        stringify: stringifyJSON
      },
      {
        identify: (value) => value == null,
        createNode: () => new Scalar.Scalar(null),
        default: true,
        tag: "tag:yaml.org,2002:null",
        test: /^null$/,
        resolve: () => null,
        stringify: stringifyJSON
      },
      {
        identify: (value) => typeof value === "boolean",
        default: true,
        tag: "tag:yaml.org,2002:bool",
        test: /^true$|^false$/,
        resolve: (str) => str === "true",
        stringify: stringifyJSON
      },
      {
        identify: intIdentify,
        default: true,
        tag: "tag:yaml.org,2002:int",
        test: /^-?(?:0|[1-9][0-9]*)$/,
        resolve: (str, _onError, { intAsBigInt }) => intAsBigInt ? BigInt(str) : parseInt(str, 10),
        stringify: ({ value }) => intIdentify(value) ? value.toString() : JSON.stringify(value)
      },
      {
        identify: (value) => typeof value === "number",
        default: true,
        tag: "tag:yaml.org,2002:float",
        test: /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?$/,
        resolve: (str) => parseFloat(str),
        stringify: stringifyJSON
      }
    ];
    var jsonError = {
      default: true,
      tag: "",
      test: /^/,
      resolve(str, onError) {
        onError(`Unresolved plain scalar ${JSON.stringify(str)}`);
        return str;
      }
    };
    var schema = [map.map, seq.seq].concat(jsonScalars, jsonError);
    exports.schema = schema;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/binary.js
var require_binary = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/binary.js"(exports) {
    "use strict";
    var node_buffer = __require("buffer");
    var Scalar = require_Scalar();
    var stringifyString = require_stringifyString();
    var binary = {
      identify: (value) => value instanceof Uint8Array,
      // Buffer inherits from Uint8Array
      default: false,
      tag: "tag:yaml.org,2002:binary",
      /**
       * Returns a Buffer in node and an Uint8Array in browsers
       *
       * To use the resulting buffer as an image, you'll want to do something like:
       *
       *   const blob = new Blob([buffer], { type: 'image/jpeg' })
       *   document.querySelector('#photo').src = URL.createObjectURL(blob)
       */
      resolve(src, onError) {
        if (typeof node_buffer.Buffer === "function") {
          return node_buffer.Buffer.from(src, "base64");
        } else if (typeof atob === "function") {
          const str = atob(src.replace(/[\n\r]/g, ""));
          const buffer = new Uint8Array(str.length);
          for (let i = 0; i < str.length; ++i)
            buffer[i] = str.charCodeAt(i);
          return buffer;
        } else {
          onError("This environment does not support reading binary tags; either Buffer or atob is required");
          return src;
        }
      },
      stringify({ comment, type, value }, ctx, onComment, onChompKeep) {
        if (!value)
          return "";
        const buf = value;
        let str;
        if (typeof node_buffer.Buffer === "function") {
          str = buf instanceof node_buffer.Buffer ? buf.toString("base64") : node_buffer.Buffer.from(buf.buffer).toString("base64");
        } else if (typeof btoa === "function") {
          let s = "";
          for (let i = 0; i < buf.length; ++i)
            s += String.fromCharCode(buf[i]);
          str = btoa(s);
        } else {
          throw new Error("This environment does not support writing binary tags; either Buffer or btoa is required");
        }
        type ?? (type = Scalar.Scalar.BLOCK_LITERAL);
        if (type !== Scalar.Scalar.QUOTE_DOUBLE) {
          const lineWidth = Math.max(ctx.options.lineWidth - ctx.indent.length, ctx.options.minContentWidth);
          const n = Math.ceil(str.length / lineWidth);
          const lines = new Array(n);
          for (let i = 0, o = 0; i < n; ++i, o += lineWidth) {
            lines[i] = str.substr(o, lineWidth);
          }
          str = lines.join(type === Scalar.Scalar.BLOCK_LITERAL ? "\n" : " ");
        }
        return stringifyString.stringifyString({ comment, type, value: str }, ctx, onComment, onChompKeep);
      }
    };
    exports.binary = binary;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/pairs.js
var require_pairs = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/pairs.js"(exports) {
    "use strict";
    var identity = require_identity();
    var Pair = require_Pair();
    var Scalar = require_Scalar();
    var YAMLSeq = require_YAMLSeq();
    function resolvePairs(seq, onError) {
      if (identity.isSeq(seq)) {
        for (let i = 0; i < seq.items.length; ++i) {
          let item = seq.items[i];
          if (identity.isPair(item))
            continue;
          else if (identity.isMap(item)) {
            if (item.items.length > 1)
              onError("Each pair must have its own sequence indicator");
            const pair = item.items[0] || new Pair.Pair(new Scalar.Scalar(null));
            if (item.commentBefore)
              pair.key.commentBefore = pair.key.commentBefore ? `${item.commentBefore}
${pair.key.commentBefore}` : item.commentBefore;
            if (item.comment) {
              const cn = pair.value ?? pair.key;
              cn.comment = cn.comment ? `${item.comment}
${cn.comment}` : item.comment;
            }
            item = pair;
          }
          seq.items[i] = identity.isPair(item) ? item : new Pair.Pair(item);
        }
      } else
        onError("Expected a sequence for this tag");
      return seq;
    }
    function createPairs(schema, iterable, ctx) {
      const { replacer } = ctx;
      const pairs2 = new YAMLSeq.YAMLSeq(schema);
      pairs2.tag = "tag:yaml.org,2002:pairs";
      let i = 0;
      if (iterable && Symbol.iterator in Object(iterable))
        for (let it of iterable) {
          if (typeof replacer === "function")
            it = replacer.call(iterable, String(i++), it);
          let key, value;
          if (Array.isArray(it)) {
            if (it.length === 2) {
              key = it[0];
              value = it[1];
            } else
              throw new TypeError(`Expected [key, value] tuple: ${it}`);
          } else if (it && it instanceof Object) {
            const keys = Object.keys(it);
            if (keys.length === 1) {
              key = keys[0];
              value = it[key];
            } else {
              throw new TypeError(`Expected tuple with one key, not ${keys.length} keys`);
            }
          } else {
            key = it;
          }
          pairs2.items.push(Pair.createPair(key, value, ctx));
        }
      return pairs2;
    }
    var pairs = {
      collection: "seq",
      default: false,
      tag: "tag:yaml.org,2002:pairs",
      resolve: resolvePairs,
      createNode: createPairs
    };
    exports.createPairs = createPairs;
    exports.pairs = pairs;
    exports.resolvePairs = resolvePairs;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/omap.js
var require_omap = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/omap.js"(exports) {
    "use strict";
    var identity = require_identity();
    var toJS = require_toJS();
    var YAMLMap = require_YAMLMap();
    var YAMLSeq = require_YAMLSeq();
    var pairs = require_pairs();
    var YAMLOMap = class _YAMLOMap extends YAMLSeq.YAMLSeq {
      constructor() {
        super();
        this.add = YAMLMap.YAMLMap.prototype.add.bind(this);
        this.delete = YAMLMap.YAMLMap.prototype.delete.bind(this);
        this.get = YAMLMap.YAMLMap.prototype.get.bind(this);
        this.has = YAMLMap.YAMLMap.prototype.has.bind(this);
        this.set = YAMLMap.YAMLMap.prototype.set.bind(this);
        this.tag = _YAMLOMap.tag;
      }
      /**
       * If `ctx` is given, the return type is actually `Map<unknown, unknown>`,
       * but TypeScript won't allow widening the signature of a child method.
       */
      toJSON(_, ctx) {
        if (!ctx)
          return super.toJSON(_);
        const map = /* @__PURE__ */ new Map();
        if (ctx?.onCreate)
          ctx.onCreate(map);
        for (const pair of this.items) {
          let key, value;
          if (identity.isPair(pair)) {
            key = toJS.toJS(pair.key, "", ctx);
            value = toJS.toJS(pair.value, key, ctx);
          } else {
            key = toJS.toJS(pair, "", ctx);
          }
          if (map.has(key))
            throw new Error("Ordered maps must not include duplicate keys");
          map.set(key, value);
        }
        return map;
      }
      static from(schema, iterable, ctx) {
        const pairs$1 = pairs.createPairs(schema, iterable, ctx);
        const omap2 = new this();
        omap2.items = pairs$1.items;
        return omap2;
      }
    };
    YAMLOMap.tag = "tag:yaml.org,2002:omap";
    var omap = {
      collection: "seq",
      identify: (value) => value instanceof Map,
      nodeClass: YAMLOMap,
      default: false,
      tag: "tag:yaml.org,2002:omap",
      resolve(seq, onError) {
        const pairs$1 = pairs.resolvePairs(seq, onError);
        const seenKeys = [];
        for (const { key } of pairs$1.items) {
          if (identity.isScalar(key)) {
            if (seenKeys.includes(key.value)) {
              onError(`Ordered maps must not include duplicate keys: ${key.value}`);
            } else {
              seenKeys.push(key.value);
            }
          }
        }
        return Object.assign(new YAMLOMap(), pairs$1);
      },
      createNode: (schema, iterable, ctx) => YAMLOMap.from(schema, iterable, ctx)
    };
    exports.YAMLOMap = YAMLOMap;
    exports.omap = omap;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/bool.js
var require_bool2 = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/bool.js"(exports) {
    "use strict";
    var Scalar = require_Scalar();
    function boolStringify({ value, source }, ctx) {
      const boolObj = value ? trueTag : falseTag;
      if (source && boolObj.test.test(source))
        return source;
      return value ? ctx.options.trueStr : ctx.options.falseStr;
    }
    var trueTag = {
      identify: (value) => value === true,
      default: true,
      tag: "tag:yaml.org,2002:bool",
      test: /^(?:Y|y|[Yy]es|YES|[Tt]rue|TRUE|[Oo]n|ON)$/,
      resolve: () => new Scalar.Scalar(true),
      stringify: boolStringify
    };
    var falseTag = {
      identify: (value) => value === false,
      default: true,
      tag: "tag:yaml.org,2002:bool",
      test: /^(?:N|n|[Nn]o|NO|[Ff]alse|FALSE|[Oo]ff|OFF)$/,
      resolve: () => new Scalar.Scalar(false),
      stringify: boolStringify
    };
    exports.falseTag = falseTag;
    exports.trueTag = trueTag;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/float.js
var require_float2 = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/float.js"(exports) {
    "use strict";
    var Scalar = require_Scalar();
    var stringifyNumber = require_stringifyNumber();
    var floatNaN = {
      identify: (value) => typeof value === "number",
      default: true,
      tag: "tag:yaml.org,2002:float",
      test: /^(?:[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)$/,
      resolve: (str) => str.slice(-3).toLowerCase() === "nan" ? NaN : str[0] === "-" ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY,
      stringify: stringifyNumber.stringifyNumber
    };
    var floatExp = {
      identify: (value) => typeof value === "number",
      default: true,
      tag: "tag:yaml.org,2002:float",
      format: "EXP",
      test: /^[-+]?(?:[0-9][0-9_]*)?(?:\.[0-9_]*)?[eE][-+]?[0-9]+$/,
      resolve: (str) => parseFloat(str.replace(/_/g, "")),
      stringify(node) {
        const num = Number(node.value);
        return isFinite(num) ? num.toExponential() : stringifyNumber.stringifyNumber(node);
      }
    };
    var float = {
      identify: (value) => typeof value === "number",
      default: true,
      tag: "tag:yaml.org,2002:float",
      test: /^[-+]?(?:[0-9][0-9_]*)?\.[0-9_]*$/,
      resolve(str) {
        const node = new Scalar.Scalar(parseFloat(str.replace(/_/g, "")));
        const dot = str.indexOf(".");
        if (dot !== -1) {
          const f = str.substring(dot + 1).replace(/_/g, "");
          if (f[f.length - 1] === "0")
            node.minFractionDigits = f.length;
        }
        return node;
      },
      stringify: stringifyNumber.stringifyNumber
    };
    exports.float = float;
    exports.floatExp = floatExp;
    exports.floatNaN = floatNaN;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/int.js
var require_int2 = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/int.js"(exports) {
    "use strict";
    var stringifyNumber = require_stringifyNumber();
    var intIdentify = (value) => typeof value === "bigint" || Number.isInteger(value);
    function intResolve(str, offset, radix, { intAsBigInt }) {
      const sign = str[0];
      if (sign === "-" || sign === "+")
        offset += 1;
      str = str.substring(offset).replace(/_/g, "");
      if (intAsBigInt) {
        switch (radix) {
          case 2:
            str = `0b${str}`;
            break;
          case 8:
            str = `0o${str}`;
            break;
          case 16:
            str = `0x${str}`;
            break;
        }
        const n2 = BigInt(str);
        return sign === "-" ? BigInt(-1) * n2 : n2;
      }
      const n = parseInt(str, radix);
      return sign === "-" ? -1 * n : n;
    }
    function intStringify(node, radix, prefix) {
      const { value } = node;
      if (intIdentify(value)) {
        const str = value.toString(radix);
        return value < 0 ? "-" + prefix + str.substr(1) : prefix + str;
      }
      return stringifyNumber.stringifyNumber(node);
    }
    var intBin = {
      identify: intIdentify,
      default: true,
      tag: "tag:yaml.org,2002:int",
      format: "BIN",
      test: /^[-+]?0b[0-1_]+$/,
      resolve: (str, _onError, opt) => intResolve(str, 2, 2, opt),
      stringify: (node) => intStringify(node, 2, "0b")
    };
    var intOct = {
      identify: intIdentify,
      default: true,
      tag: "tag:yaml.org,2002:int",
      format: "OCT",
      test: /^[-+]?0[0-7_]+$/,
      resolve: (str, _onError, opt) => intResolve(str, 1, 8, opt),
      stringify: (node) => intStringify(node, 8, "0")
    };
    var int = {
      identify: intIdentify,
      default: true,
      tag: "tag:yaml.org,2002:int",
      test: /^[-+]?[0-9][0-9_]*$/,
      resolve: (str, _onError, opt) => intResolve(str, 0, 10, opt),
      stringify: stringifyNumber.stringifyNumber
    };
    var intHex = {
      identify: intIdentify,
      default: true,
      tag: "tag:yaml.org,2002:int",
      format: "HEX",
      test: /^[-+]?0x[0-9a-fA-F_]+$/,
      resolve: (str, _onError, opt) => intResolve(str, 2, 16, opt),
      stringify: (node) => intStringify(node, 16, "0x")
    };
    exports.int = int;
    exports.intBin = intBin;
    exports.intHex = intHex;
    exports.intOct = intOct;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/set.js
var require_set = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/set.js"(exports) {
    "use strict";
    var identity = require_identity();
    var Pair = require_Pair();
    var YAMLMap = require_YAMLMap();
    var YAMLSet = class _YAMLSet extends YAMLMap.YAMLMap {
      constructor(schema) {
        super(schema);
        this.tag = _YAMLSet.tag;
      }
      add(key) {
        let pair;
        if (identity.isPair(key))
          pair = key;
        else if (key && typeof key === "object" && "key" in key && "value" in key && key.value === null)
          pair = new Pair.Pair(key.key, null);
        else
          pair = new Pair.Pair(key, null);
        const prev = YAMLMap.findPair(this.items, pair.key);
        if (!prev)
          this.items.push(pair);
      }
      /**
       * If `keepPair` is `true`, returns the Pair matching `key`.
       * Otherwise, returns the value of that Pair's key.
       */
      get(key, keepPair) {
        const pair = YAMLMap.findPair(this.items, key);
        return !keepPair && identity.isPair(pair) ? identity.isScalar(pair.key) ? pair.key.value : pair.key : pair;
      }
      set(key, value) {
        if (typeof value !== "boolean")
          throw new Error(`Expected boolean value for set(key, value) in a YAML set, not ${typeof value}`);
        const prev = YAMLMap.findPair(this.items, key);
        if (prev && !value) {
          this.items.splice(this.items.indexOf(prev), 1);
        } else if (!prev && value) {
          this.items.push(new Pair.Pair(key));
        }
      }
      toJSON(_, ctx) {
        return super.toJSON(_, ctx, Set);
      }
      toString(ctx, onComment, onChompKeep) {
        if (!ctx)
          return JSON.stringify(this);
        if (this.hasAllNullValues(true))
          return super.toString(Object.assign({}, ctx, { allNullValues: true }), onComment, onChompKeep);
        else
          throw new Error("Set items must all have null values");
      }
      static from(schema, iterable, ctx) {
        const { replacer } = ctx;
        const set2 = new this(schema);
        if (iterable && Symbol.iterator in Object(iterable))
          for (let value of iterable) {
            if (typeof replacer === "function")
              value = replacer.call(iterable, value, value);
            set2.items.push(Pair.createPair(value, null, ctx));
          }
        return set2;
      }
    };
    YAMLSet.tag = "tag:yaml.org,2002:set";
    var set = {
      collection: "map",
      identify: (value) => value instanceof Set,
      nodeClass: YAMLSet,
      default: false,
      tag: "tag:yaml.org,2002:set",
      createNode: (schema, iterable, ctx) => YAMLSet.from(schema, iterable, ctx),
      resolve(map, onError) {
        if (identity.isMap(map)) {
          if (map.hasAllNullValues(true))
            return Object.assign(new YAMLSet(), map);
          else
            onError("Set items must all have null values");
        } else
          onError("Expected a mapping for this tag");
        return map;
      }
    };
    exports.YAMLSet = YAMLSet;
    exports.set = set;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/timestamp.js
var require_timestamp = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/timestamp.js"(exports) {
    "use strict";
    var stringifyNumber = require_stringifyNumber();
    function parseSexagesimal(str, asBigInt) {
      const sign = str[0];
      const parts = sign === "-" || sign === "+" ? str.substring(1) : str;
      const num = (n) => asBigInt ? BigInt(n) : Number(n);
      const res = parts.replace(/_/g, "").split(":").reduce((res2, p) => res2 * num(60) + num(p), num(0));
      return sign === "-" ? num(-1) * res : res;
    }
    function stringifySexagesimal(node) {
      let { value } = node;
      let num = (n) => n;
      if (typeof value === "bigint")
        num = (n) => BigInt(n);
      else if (isNaN(value) || !isFinite(value))
        return stringifyNumber.stringifyNumber(node);
      let sign = "";
      if (value < 0) {
        sign = "-";
        value *= num(-1);
      }
      const _60 = num(60);
      const parts = [value % _60];
      if (value < 60) {
        parts.unshift(0);
      } else {
        value = (value - parts[0]) / _60;
        parts.unshift(value % _60);
        if (value >= 60) {
          value = (value - parts[0]) / _60;
          parts.unshift(value);
        }
      }
      return sign + parts.map((n) => String(n).padStart(2, "0")).join(":").replace(/000000\d*$/, "");
    }
    var intTime = {
      identify: (value) => typeof value === "bigint" || Number.isInteger(value),
      default: true,
      tag: "tag:yaml.org,2002:int",
      format: "TIME",
      test: /^[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+$/,
      resolve: (str, _onError, { intAsBigInt }) => parseSexagesimal(str, intAsBigInt),
      stringify: stringifySexagesimal
    };
    var floatTime = {
      identify: (value) => typeof value === "number",
      default: true,
      tag: "tag:yaml.org,2002:float",
      format: "TIME",
      test: /^[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*$/,
      resolve: (str) => parseSexagesimal(str, false),
      stringify: stringifySexagesimal
    };
    var timestamp5 = {
      identify: (value) => value instanceof Date,
      default: true,
      tag: "tag:yaml.org,2002:timestamp",
      // If the time zone is omitted, the timestamp is assumed to be specified in UTC. The time part
      // may be omitted altogether, resulting in a date format. In such a case, the time part is
      // assumed to be 00:00:00Z (start of day, UTC).
      test: RegExp("^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})(?:(?:t|T|[ \\t]+)([0-9]{1,2}):([0-9]{1,2}):([0-9]{1,2}(\\.[0-9]+)?)(?:[ \\t]*(Z|[-+][012]?[0-9](?::[0-9]{2})?))?)?$"),
      resolve(str) {
        const match = str.match(timestamp5.test);
        if (!match)
          throw new Error("!!timestamp expects a date, starting with yyyy-mm-dd");
        const [, year, month, day, hour, minute, second] = match.map(Number);
        const millisec = match[7] ? Number((match[7] + "00").substr(1, 3)) : 0;
        let date = Date.UTC(year, month - 1, day, hour || 0, minute || 0, second || 0, millisec);
        const tz = match[8];
        if (tz && tz !== "Z") {
          let d = parseSexagesimal(tz, false);
          if (Math.abs(d) < 30)
            d *= 60;
          date -= 6e4 * d;
        }
        return new Date(date);
      },
      stringify: ({ value }) => value?.toISOString().replace(/(T00:00:00)?\.000Z$/, "") ?? ""
    };
    exports.floatTime = floatTime;
    exports.intTime = intTime;
    exports.timestamp = timestamp5;
  }
});

// node_modules/yaml/dist/schema/yaml-1.1/schema.js
var require_schema3 = __commonJS({
  "node_modules/yaml/dist/schema/yaml-1.1/schema.js"(exports) {
    "use strict";
    var map = require_map();
    var _null = require_null();
    var seq = require_seq();
    var string = require_string();
    var binary = require_binary();
    var bool = require_bool2();
    var float = require_float2();
    var int = require_int2();
    var merge = require_merge();
    var omap = require_omap();
    var pairs = require_pairs();
    var set = require_set();
    var timestamp5 = require_timestamp();
    var schema = [
      map.map,
      seq.seq,
      string.string,
      _null.nullTag,
      bool.trueTag,
      bool.falseTag,
      int.intBin,
      int.intOct,
      int.int,
      int.intHex,
      float.floatNaN,
      float.floatExp,
      float.float,
      binary.binary,
      merge.merge,
      omap.omap,
      pairs.pairs,
      set.set,
      timestamp5.intTime,
      timestamp5.floatTime,
      timestamp5.timestamp
    ];
    exports.schema = schema;
  }
});

// node_modules/yaml/dist/schema/tags.js
var require_tags = __commonJS({
  "node_modules/yaml/dist/schema/tags.js"(exports) {
    "use strict";
    var map = require_map();
    var _null = require_null();
    var seq = require_seq();
    var string = require_string();
    var bool = require_bool();
    var float = require_float();
    var int = require_int();
    var schema = require_schema();
    var schema$1 = require_schema2();
    var binary = require_binary();
    var merge = require_merge();
    var omap = require_omap();
    var pairs = require_pairs();
    var schema$2 = require_schema3();
    var set = require_set();
    var timestamp5 = require_timestamp();
    var schemas = /* @__PURE__ */ new Map([
      ["core", schema.schema],
      ["failsafe", [map.map, seq.seq, string.string]],
      ["json", schema$1.schema],
      ["yaml11", schema$2.schema],
      ["yaml-1.1", schema$2.schema]
    ]);
    var tagsByName = {
      binary: binary.binary,
      bool: bool.boolTag,
      float: float.float,
      floatExp: float.floatExp,
      floatNaN: float.floatNaN,
      floatTime: timestamp5.floatTime,
      int: int.int,
      intHex: int.intHex,
      intOct: int.intOct,
      intTime: timestamp5.intTime,
      map: map.map,
      merge: merge.merge,
      null: _null.nullTag,
      omap: omap.omap,
      pairs: pairs.pairs,
      seq: seq.seq,
      set: set.set,
      timestamp: timestamp5.timestamp
    };
    var coreKnownTags = {
      "tag:yaml.org,2002:binary": binary.binary,
      "tag:yaml.org,2002:merge": merge.merge,
      "tag:yaml.org,2002:omap": omap.omap,
      "tag:yaml.org,2002:pairs": pairs.pairs,
      "tag:yaml.org,2002:set": set.set,
      "tag:yaml.org,2002:timestamp": timestamp5.timestamp
    };
    function getTags(customTags, schemaName, addMergeTag) {
      const schemaTags = schemas.get(schemaName);
      if (schemaTags && !customTags) {
        return addMergeTag && !schemaTags.includes(merge.merge) ? schemaTags.concat(merge.merge) : schemaTags.slice();
      }
      let tags = schemaTags;
      if (!tags) {
        if (Array.isArray(customTags))
          tags = [];
        else {
          const keys = Array.from(schemas.keys()).filter((key) => key !== "yaml11").map((key) => JSON.stringify(key)).join(", ");
          throw new Error(`Unknown schema "${schemaName}"; use one of ${keys} or define customTags array`);
        }
      }
      if (Array.isArray(customTags)) {
        for (const tag of customTags)
          tags = tags.concat(tag);
      } else if (typeof customTags === "function") {
        tags = customTags(tags.slice());
      }
      if (addMergeTag)
        tags = tags.concat(merge.merge);
      return tags.reduce((tags2, tag) => {
        const tagObj = typeof tag === "string" ? tagsByName[tag] : tag;
        if (!tagObj) {
          const tagName = JSON.stringify(tag);
          const keys = Object.keys(tagsByName).map((key) => JSON.stringify(key)).join(", ");
          throw new Error(`Unknown custom tag ${tagName}; use one of ${keys}`);
        }
        if (!tags2.includes(tagObj))
          tags2.push(tagObj);
        return tags2;
      }, []);
    }
    exports.coreKnownTags = coreKnownTags;
    exports.getTags = getTags;
  }
});

// node_modules/yaml/dist/schema/Schema.js
var require_Schema = __commonJS({
  "node_modules/yaml/dist/schema/Schema.js"(exports) {
    "use strict";
    var identity = require_identity();
    var map = require_map();
    var seq = require_seq();
    var string = require_string();
    var tags = require_tags();
    var sortMapEntriesByKey = (a, b) => a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
    var Schema = class _Schema {
      constructor({ compat, customTags, merge, resolveKnownTags, schema, sortMapEntries, toStringDefaults }) {
        this.compat = Array.isArray(compat) ? tags.getTags(compat, "compat") : compat ? tags.getTags(null, compat) : null;
        this.name = typeof schema === "string" && schema || "core";
        this.knownTags = resolveKnownTags ? tags.coreKnownTags : {};
        this.tags = tags.getTags(customTags, this.name, merge);
        this.toStringOptions = toStringDefaults ?? null;
        Object.defineProperty(this, identity.MAP, { value: map.map });
        Object.defineProperty(this, identity.SCALAR, { value: string.string });
        Object.defineProperty(this, identity.SEQ, { value: seq.seq });
        this.sortMapEntries = typeof sortMapEntries === "function" ? sortMapEntries : sortMapEntries === true ? sortMapEntriesByKey : null;
      }
      clone() {
        const copy = Object.create(_Schema.prototype, Object.getOwnPropertyDescriptors(this));
        copy.tags = this.tags.slice();
        return copy;
      }
    };
    exports.Schema = Schema;
  }
});

// node_modules/yaml/dist/stringify/stringifyDocument.js
var require_stringifyDocument = __commonJS({
  "node_modules/yaml/dist/stringify/stringifyDocument.js"(exports) {
    "use strict";
    var identity = require_identity();
    var stringify = require_stringify();
    var stringifyComment = require_stringifyComment();
    function stringifyDocument(doc, options) {
      const lines = [];
      let hasDirectives = options.directives === true;
      if (options.directives !== false && doc.directives) {
        const dir = doc.directives.toString(doc);
        if (dir) {
          lines.push(dir);
          hasDirectives = true;
        } else if (doc.directives.docStart)
          hasDirectives = true;
      }
      if (hasDirectives)
        lines.push("---");
      const ctx = stringify.createStringifyContext(doc, options);
      const { commentString } = ctx.options;
      if (doc.commentBefore) {
        if (lines.length !== 1)
          lines.unshift("");
        const cs = commentString(doc.commentBefore);
        lines.unshift(stringifyComment.indentComment(cs, ""));
      }
      let chompKeep = false;
      let contentComment = null;
      if (doc.contents) {
        if (identity.isNode(doc.contents)) {
          if (doc.contents.spaceBefore && hasDirectives)
            lines.push("");
          if (doc.contents.commentBefore) {
            const cs = commentString(doc.contents.commentBefore);
            lines.push(stringifyComment.indentComment(cs, ""));
          }
          ctx.forceBlockIndent = !!doc.comment;
          contentComment = doc.contents.comment;
        }
        const onChompKeep = contentComment ? void 0 : () => chompKeep = true;
        let body = stringify.stringify(doc.contents, ctx, () => contentComment = null, onChompKeep);
        if (contentComment)
          body += stringifyComment.lineComment(body, "", commentString(contentComment));
        if ((body[0] === "|" || body[0] === ">") && lines[lines.length - 1] === "---") {
          lines[lines.length - 1] = `--- ${body}`;
        } else
          lines.push(body);
      } else {
        lines.push(stringify.stringify(doc.contents, ctx));
      }
      if (doc.directives?.docEnd) {
        if (doc.comment) {
          const cs = commentString(doc.comment);
          if (cs.includes("\n")) {
            lines.push("...");
            lines.push(stringifyComment.indentComment(cs, ""));
          } else {
            lines.push(`... ${cs}`);
          }
        } else {
          lines.push("...");
        }
      } else {
        let dc = doc.comment;
        if (dc && chompKeep)
          dc = dc.replace(/^\n+/, "");
        if (dc) {
          if ((!chompKeep || contentComment) && lines[lines.length - 1] !== "")
            lines.push("");
          lines.push(stringifyComment.indentComment(commentString(dc), ""));
        }
      }
      return lines.join("\n") + "\n";
    }
    exports.stringifyDocument = stringifyDocument;
  }
});

// node_modules/yaml/dist/doc/Document.js
var require_Document = __commonJS({
  "node_modules/yaml/dist/doc/Document.js"(exports) {
    "use strict";
    var Alias = require_Alias();
    var Collection = require_Collection();
    var identity = require_identity();
    var Pair = require_Pair();
    var toJS = require_toJS();
    var Schema = require_Schema();
    var stringifyDocument = require_stringifyDocument();
    var anchors = require_anchors();
    var applyReviver = require_applyReviver();
    var createNode = require_createNode();
    var directives = require_directives();
    var Document = class _Document {
      constructor(value, replacer, options) {
        this.commentBefore = null;
        this.comment = null;
        this.errors = [];
        this.warnings = [];
        Object.defineProperty(this, identity.NODE_TYPE, { value: identity.DOC });
        let _replacer = null;
        if (typeof replacer === "function" || Array.isArray(replacer)) {
          _replacer = replacer;
        } else if (options === void 0 && replacer) {
          options = replacer;
          replacer = void 0;
        }
        const opt = Object.assign({
          intAsBigInt: false,
          keepSourceTokens: false,
          logLevel: "warn",
          prettyErrors: true,
          strict: true,
          stringKeys: false,
          uniqueKeys: true,
          version: "1.2"
        }, options);
        this.options = opt;
        let { version } = opt;
        if (options?._directives) {
          this.directives = options._directives.atDocument();
          if (this.directives.yaml.explicit)
            version = this.directives.yaml.version;
        } else
          this.directives = new directives.Directives({ version });
        this.setSchema(version, options);
        this.contents = value === void 0 ? null : this.createNode(value, _replacer, options);
      }
      /**
       * Create a deep copy of this Document and its contents.
       *
       * Custom Node values that inherit from `Object` still refer to their original instances.
       */
      clone() {
        const copy = Object.create(_Document.prototype, {
          [identity.NODE_TYPE]: { value: identity.DOC }
        });
        copy.commentBefore = this.commentBefore;
        copy.comment = this.comment;
        copy.errors = this.errors.slice();
        copy.warnings = this.warnings.slice();
        copy.options = Object.assign({}, this.options);
        if (this.directives)
          copy.directives = this.directives.clone();
        copy.schema = this.schema.clone();
        copy.contents = identity.isNode(this.contents) ? this.contents.clone(copy.schema) : this.contents;
        if (this.range)
          copy.range = this.range.slice();
        return copy;
      }
      /** Adds a value to the document. */
      add(value) {
        if (assertCollection(this.contents))
          this.contents.add(value);
      }
      /** Adds a value to the document. */
      addIn(path16, value) {
        if (assertCollection(this.contents))
          this.contents.addIn(path16, value);
      }
      /**
       * Create a new `Alias` node, ensuring that the target `node` has the required anchor.
       *
       * If `node` already has an anchor, `name` is ignored.
       * Otherwise, the `node.anchor` value will be set to `name`,
       * or if an anchor with that name is already present in the document,
       * `name` will be used as a prefix for a new unique anchor.
       * If `name` is undefined, the generated anchor will use 'a' as a prefix.
       */
      createAlias(node, name) {
        if (!node.anchor) {
          const prev = anchors.anchorNames(this);
          node.anchor = // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
          !name || prev.has(name) ? anchors.findNewAnchor(name || "a", prev) : name;
        }
        return new Alias.Alias(node.anchor);
      }
      createNode(value, replacer, options) {
        let _replacer = void 0;
        if (typeof replacer === "function") {
          value = replacer.call({ "": value }, "", value);
          _replacer = replacer;
        } else if (Array.isArray(replacer)) {
          const keyToStr = (v) => typeof v === "number" || v instanceof String || v instanceof Number;
          const asStr = replacer.filter(keyToStr).map(String);
          if (asStr.length > 0)
            replacer = replacer.concat(asStr);
          _replacer = replacer;
        } else if (options === void 0 && replacer) {
          options = replacer;
          replacer = void 0;
        }
        const { aliasDuplicateObjects, anchorPrefix, flow, keepUndefined, onTagObj, tag } = options ?? {};
        const { onAnchor, setAnchors, sourceObjects } = anchors.createNodeAnchors(
          this,
          // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
          anchorPrefix || "a"
        );
        const ctx = {
          aliasDuplicateObjects: aliasDuplicateObjects ?? true,
          keepUndefined: keepUndefined ?? false,
          onAnchor,
          onTagObj,
          replacer: _replacer,
          schema: this.schema,
          sourceObjects
        };
        const node = createNode.createNode(value, tag, ctx);
        if (flow && identity.isCollection(node))
          node.flow = true;
        setAnchors();
        return node;
      }
      /**
       * Convert a key and a value into a `Pair` using the current schema,
       * recursively wrapping all values as `Scalar` or `Collection` nodes.
       */
      createPair(key, value, options = {}) {
        const k = this.createNode(key, null, options);
        const v = this.createNode(value, null, options);
        return new Pair.Pair(k, v);
      }
      /**
       * Removes a value from the document.
       * @returns `true` if the item was found and removed.
       */
      delete(key) {
        return assertCollection(this.contents) ? this.contents.delete(key) : false;
      }
      /**
       * Removes a value from the document.
       * @returns `true` if the item was found and removed.
       */
      deleteIn(path16) {
        if (Collection.isEmptyPath(path16)) {
          if (this.contents == null)
            return false;
          this.contents = null;
          return true;
        }
        return assertCollection(this.contents) ? this.contents.deleteIn(path16) : false;
      }
      /**
       * Returns item at `key`, or `undefined` if not found. By default unwraps
       * scalar values from their surrounding node; to disable set `keepScalar` to
       * `true` (collections are always returned intact).
       */
      get(key, keepScalar) {
        return identity.isCollection(this.contents) ? this.contents.get(key, keepScalar) : void 0;
      }
      /**
       * Returns item at `path`, or `undefined` if not found. By default unwraps
       * scalar values from their surrounding node; to disable set `keepScalar` to
       * `true` (collections are always returned intact).
       */
      getIn(path16, keepScalar) {
        if (Collection.isEmptyPath(path16))
          return !keepScalar && identity.isScalar(this.contents) ? this.contents.value : this.contents;
        return identity.isCollection(this.contents) ? this.contents.getIn(path16, keepScalar) : void 0;
      }
      /**
       * Checks if the document includes a value with the key `key`.
       */
      has(key) {
        return identity.isCollection(this.contents) ? this.contents.has(key) : false;
      }
      /**
       * Checks if the document includes a value at `path`.
       */
      hasIn(path16) {
        if (Collection.isEmptyPath(path16))
          return this.contents !== void 0;
        return identity.isCollection(this.contents) ? this.contents.hasIn(path16) : false;
      }
      /**
       * Sets a value in this document. For `!!set`, `value` needs to be a
       * boolean to add/remove the item from the set.
       */
      set(key, value) {
        if (this.contents == null) {
          this.contents = Collection.collectionFromPath(this.schema, [key], value);
        } else if (assertCollection(this.contents)) {
          this.contents.set(key, value);
        }
      }
      /**
       * Sets a value in this document. For `!!set`, `value` needs to be a
       * boolean to add/remove the item from the set.
       */
      setIn(path16, value) {
        if (Collection.isEmptyPath(path16)) {
          this.contents = value;
        } else if (this.contents == null) {
          this.contents = Collection.collectionFromPath(this.schema, Array.from(path16), value);
        } else if (assertCollection(this.contents)) {
          this.contents.setIn(path16, value);
        }
      }
      /**
       * Change the YAML version and schema used by the document.
       * A `null` version disables support for directives, explicit tags, anchors, and aliases.
       * It also requires the `schema` option to be given as a `Schema` instance value.
       *
       * Overrides all previously set schema options.
       */
      setSchema(version, options = {}) {
        if (typeof version === "number")
          version = String(version);
        let opt;
        switch (version) {
          case "1.1":
            if (this.directives)
              this.directives.yaml.version = "1.1";
            else
              this.directives = new directives.Directives({ version: "1.1" });
            opt = { resolveKnownTags: false, schema: "yaml-1.1" };
            break;
          case "1.2":
          case "next":
            if (this.directives)
              this.directives.yaml.version = version;
            else
              this.directives = new directives.Directives({ version });
            opt = { resolveKnownTags: true, schema: "core" };
            break;
          case null:
            if (this.directives)
              delete this.directives;
            opt = null;
            break;
          default: {
            const sv = JSON.stringify(version);
            throw new Error(`Expected '1.1', '1.2' or null as first argument, but found: ${sv}`);
          }
        }
        if (options.schema instanceof Object)
          this.schema = options.schema;
        else if (opt)
          this.schema = new Schema.Schema(Object.assign(opt, options));
        else
          throw new Error(`With a null YAML version, the { schema: Schema } option is required`);
      }
      // json & jsonArg are only used from toJSON()
      toJS({ json: json7, jsonArg, mapAsMap, maxAliasCount, onAnchor, reviver } = {}) {
        const ctx = {
          anchors: /* @__PURE__ */ new Map(),
          doc: this,
          keep: !json7,
          mapAsMap: mapAsMap === true,
          mapKeyWarned: false,
          maxAliasCount: typeof maxAliasCount === "number" ? maxAliasCount : 100
        };
        const res = toJS.toJS(this.contents, jsonArg ?? "", ctx);
        if (typeof onAnchor === "function")
          for (const { count, res: res2 } of ctx.anchors.values())
            onAnchor(res2, count);
        return typeof reviver === "function" ? applyReviver.applyReviver(reviver, { "": res }, "", res) : res;
      }
      /**
       * A JSON representation of the document `contents`.
       *
       * @param jsonArg Used by `JSON.stringify` to indicate the array index or
       *   property name.
       */
      toJSON(jsonArg, onAnchor) {
        return this.toJS({ json: true, jsonArg, mapAsMap: false, onAnchor });
      }
      /** A YAML representation of the document. */
      toString(options = {}) {
        if (this.errors.length > 0)
          throw new Error("Document with errors cannot be stringified");
        if ("indent" in options && (!Number.isInteger(options.indent) || Number(options.indent) <= 0)) {
          const s = JSON.stringify(options.indent);
          throw new Error(`"indent" option must be a positive integer, not ${s}`);
        }
        return stringifyDocument.stringifyDocument(this, options);
      }
    };
    function assertCollection(contents) {
      if (identity.isCollection(contents))
        return true;
      throw new Error("Expected a YAML collection as document contents");
    }
    exports.Document = Document;
  }
});

// node_modules/yaml/dist/errors.js
var require_errors = __commonJS({
  "node_modules/yaml/dist/errors.js"(exports) {
    "use strict";
    var YAMLError = class extends Error {
      constructor(name, pos, code, message) {
        super();
        this.name = name;
        this.code = code;
        this.message = message;
        this.pos = pos;
      }
    };
    var YAMLParseError = class extends YAMLError {
      constructor(pos, code, message) {
        super("YAMLParseError", pos, code, message);
      }
    };
    var YAMLWarning = class extends YAMLError {
      constructor(pos, code, message) {
        super("YAMLWarning", pos, code, message);
      }
    };
    var prettifyError = (src, lc) => (error) => {
      if (error.pos[0] === -1)
        return;
      error.linePos = error.pos.map((pos) => lc.linePos(pos));
      const { line, col } = error.linePos[0];
      error.message += ` at line ${line}, column ${col}`;
      let ci = col - 1;
      let lineStr = src.substring(lc.lineStarts[line - 1], lc.lineStarts[line]).replace(/[\n\r]+$/, "");
      if (ci >= 60 && lineStr.length > 80) {
        const trimStart = Math.min(ci - 39, lineStr.length - 79);
        lineStr = "\u2026" + lineStr.substring(trimStart);
        ci -= trimStart - 1;
      }
      if (lineStr.length > 80)
        lineStr = lineStr.substring(0, 79) + "\u2026";
      if (line > 1 && /^ *$/.test(lineStr.substring(0, ci))) {
        let prev = src.substring(lc.lineStarts[line - 2], lc.lineStarts[line - 1]);
        if (prev.length > 80)
          prev = prev.substring(0, 79) + "\u2026\n";
        lineStr = prev + lineStr;
      }
      if (/[^ ]/.test(lineStr)) {
        let count = 1;
        const end = error.linePos[1];
        if (end?.line === line && end.col > col) {
          count = Math.max(1, Math.min(end.col - col, 80 - ci));
        }
        const pointer = " ".repeat(ci) + "^".repeat(count);
        error.message += `:

${lineStr}
${pointer}
`;
      }
    };
    exports.YAMLError = YAMLError;
    exports.YAMLParseError = YAMLParseError;
    exports.YAMLWarning = YAMLWarning;
    exports.prettifyError = prettifyError;
  }
});

// node_modules/yaml/dist/compose/resolve-props.js
var require_resolve_props = __commonJS({
  "node_modules/yaml/dist/compose/resolve-props.js"(exports) {
    "use strict";
    function resolveProps(tokens, { flow, indicator, next, offset, onError, parentIndent, startOnNewline }) {
      let spaceBefore = false;
      let atNewline = startOnNewline;
      let hasSpace = startOnNewline;
      let comment = "";
      let commentSep = "";
      let hasNewline = false;
      let reqSpace = false;
      let tab = null;
      let anchor = null;
      let tag = null;
      let newlineAfterProp = null;
      let comma = null;
      let found = null;
      let start = null;
      for (const token of tokens) {
        if (reqSpace) {
          if (token.type !== "space" && token.type !== "newline" && token.type !== "comma")
            onError(token.offset, "MISSING_CHAR", "Tags and anchors must be separated from the next token by white space");
          reqSpace = false;
        }
        if (tab) {
          if (atNewline && token.type !== "comment" && token.type !== "newline") {
            onError(tab, "TAB_AS_INDENT", "Tabs are not allowed as indentation");
          }
          tab = null;
        }
        switch (token.type) {
          case "space":
            if (!flow && (indicator !== "doc-start" || next?.type !== "flow-collection") && token.source.includes("	")) {
              tab = token;
            }
            hasSpace = true;
            break;
          case "comment": {
            if (!hasSpace)
              onError(token, "MISSING_CHAR", "Comments must be separated from other tokens by white space characters");
            const cb = token.source.substring(1) || " ";
            if (!comment)
              comment = cb;
            else
              comment += commentSep + cb;
            commentSep = "";
            atNewline = false;
            break;
          }
          case "newline":
            if (atNewline) {
              if (comment)
                comment += token.source;
              else if (!found || indicator !== "seq-item-ind")
                spaceBefore = true;
            } else
              commentSep += token.source;
            atNewline = true;
            hasNewline = true;
            if (anchor || tag)
              newlineAfterProp = token;
            hasSpace = true;
            break;
          case "anchor":
            if (anchor)
              onError(token, "MULTIPLE_ANCHORS", "A node can have at most one anchor");
            if (token.source.endsWith(":"))
              onError(token.offset + token.source.length - 1, "BAD_ALIAS", "Anchor ending in : is ambiguous", true);
            anchor = token;
            start ?? (start = token.offset);
            atNewline = false;
            hasSpace = false;
            reqSpace = true;
            break;
          case "tag": {
            if (tag)
              onError(token, "MULTIPLE_TAGS", "A node can have at most one tag");
            tag = token;
            start ?? (start = token.offset);
            atNewline = false;
            hasSpace = false;
            reqSpace = true;
            break;
          }
          case indicator:
            if (anchor || tag)
              onError(token, "BAD_PROP_ORDER", `Anchors and tags must be after the ${token.source} indicator`);
            if (found)
              onError(token, "UNEXPECTED_TOKEN", `Unexpected ${token.source} in ${flow ?? "collection"}`);
            found = token;
            atNewline = indicator === "seq-item-ind" || indicator === "explicit-key-ind";
            hasSpace = false;
            break;
          case "comma":
            if (flow) {
              if (comma)
                onError(token, "UNEXPECTED_TOKEN", `Unexpected , in ${flow}`);
              comma = token;
              atNewline = false;
              hasSpace = false;
              break;
            }
          // else fallthrough
          default:
            onError(token, "UNEXPECTED_TOKEN", `Unexpected ${token.type} token`);
            atNewline = false;
            hasSpace = false;
        }
      }
      const last = tokens[tokens.length - 1];
      const end = last ? last.offset + last.source.length : offset;
      if (reqSpace && next && next.type !== "space" && next.type !== "newline" && next.type !== "comma" && (next.type !== "scalar" || next.source !== "")) {
        onError(next.offset, "MISSING_CHAR", "Tags and anchors must be separated from the next token by white space");
      }
      if (tab && (atNewline && tab.indent <= parentIndent || next?.type === "block-map" || next?.type === "block-seq"))
        onError(tab, "TAB_AS_INDENT", "Tabs are not allowed as indentation");
      return {
        comma,
        found,
        spaceBefore,
        comment,
        hasNewline,
        anchor,
        tag,
        newlineAfterProp,
        end,
        start: start ?? end
      };
    }
    exports.resolveProps = resolveProps;
  }
});

// node_modules/yaml/dist/compose/util-contains-newline.js
var require_util_contains_newline = __commonJS({
  "node_modules/yaml/dist/compose/util-contains-newline.js"(exports) {
    "use strict";
    function containsNewline(key) {
      if (!key)
        return null;
      switch (key.type) {
        case "alias":
        case "scalar":
        case "double-quoted-scalar":
        case "single-quoted-scalar":
          if (key.source.includes("\n"))
            return true;
          if (key.end) {
            for (const st of key.end)
              if (st.type === "newline")
                return true;
          }
          return false;
        case "flow-collection":
          for (const it of key.items) {
            for (const st of it.start)
              if (st.type === "newline")
                return true;
            if (it.sep) {
              for (const st of it.sep)
                if (st.type === "newline")
                  return true;
            }
            if (containsNewline(it.key) || containsNewline(it.value))
              return true;
          }
          return false;
        default:
          return true;
      }
    }
    exports.containsNewline = containsNewline;
  }
});

// node_modules/yaml/dist/compose/util-flow-indent-check.js
var require_util_flow_indent_check = __commonJS({
  "node_modules/yaml/dist/compose/util-flow-indent-check.js"(exports) {
    "use strict";
    var utilContainsNewline = require_util_contains_newline();
    function flowIndentCheck(indent, fc, onError) {
      if (fc?.type === "flow-collection") {
        const end = fc.end[0];
        if (end.indent === indent && (end.source === "]" || end.source === "}") && utilContainsNewline.containsNewline(fc)) {
          const msg = "Flow end indicator should be more indented than parent";
          onError(end, "BAD_INDENT", msg, true);
        }
      }
    }
    exports.flowIndentCheck = flowIndentCheck;
  }
});

// node_modules/yaml/dist/compose/util-map-includes.js
var require_util_map_includes = __commonJS({
  "node_modules/yaml/dist/compose/util-map-includes.js"(exports) {
    "use strict";
    var identity = require_identity();
    function mapIncludes(ctx, items, search) {
      const { uniqueKeys } = ctx.options;
      if (uniqueKeys === false)
        return false;
      const isEqual = typeof uniqueKeys === "function" ? uniqueKeys : (a, b) => a === b || identity.isScalar(a) && identity.isScalar(b) && a.value === b.value;
      return items.some((pair) => isEqual(pair.key, search));
    }
    exports.mapIncludes = mapIncludes;
  }
});

// node_modules/yaml/dist/compose/resolve-block-map.js
var require_resolve_block_map = __commonJS({
  "node_modules/yaml/dist/compose/resolve-block-map.js"(exports) {
    "use strict";
    var Pair = require_Pair();
    var YAMLMap = require_YAMLMap();
    var resolveProps = require_resolve_props();
    var utilContainsNewline = require_util_contains_newline();
    var utilFlowIndentCheck = require_util_flow_indent_check();
    var utilMapIncludes = require_util_map_includes();
    var startColMsg = "All mapping items must start at the same column";
    function resolveBlockMap({ composeNode, composeEmptyNode }, ctx, bm, onError, tag) {
      const NodeClass = tag?.nodeClass ?? YAMLMap.YAMLMap;
      const map = new NodeClass(ctx.schema);
      if (ctx.atRoot)
        ctx.atRoot = false;
      let offset = bm.offset;
      let commentEnd = null;
      for (const collItem of bm.items) {
        const { start, key, sep, value } = collItem;
        const keyProps = resolveProps.resolveProps(start, {
          indicator: "explicit-key-ind",
          next: key ?? sep?.[0],
          offset,
          onError,
          parentIndent: bm.indent,
          startOnNewline: true
        });
        const implicitKey = !keyProps.found;
        if (implicitKey) {
          if (key) {
            if (key.type === "block-seq")
              onError(offset, "BLOCK_AS_IMPLICIT_KEY", "A block sequence may not be used as an implicit map key");
            else if ("indent" in key && key.indent !== bm.indent)
              onError(offset, "BAD_INDENT", startColMsg);
          }
          if (!keyProps.anchor && !keyProps.tag && !sep) {
            commentEnd = keyProps.end;
            if (keyProps.comment) {
              if (map.comment)
                map.comment += "\n" + keyProps.comment;
              else
                map.comment = keyProps.comment;
            }
            continue;
          }
          if (keyProps.newlineAfterProp || utilContainsNewline.containsNewline(key)) {
            onError(key ?? start[start.length - 1], "MULTILINE_IMPLICIT_KEY", "Implicit keys need to be on a single line");
          }
        } else if (keyProps.found?.indent !== bm.indent) {
          onError(offset, "BAD_INDENT", startColMsg);
        }
        ctx.atKey = true;
        const keyStart = keyProps.end;
        const keyNode = key ? composeNode(ctx, key, keyProps, onError) : composeEmptyNode(ctx, keyStart, start, null, keyProps, onError);
        if (ctx.schema.compat)
          utilFlowIndentCheck.flowIndentCheck(bm.indent, key, onError);
        ctx.atKey = false;
        if (utilMapIncludes.mapIncludes(ctx, map.items, keyNode))
          onError(keyStart, "DUPLICATE_KEY", "Map keys must be unique");
        const valueProps = resolveProps.resolveProps(sep ?? [], {
          indicator: "map-value-ind",
          next: value,
          offset: keyNode.range[2],
          onError,
          parentIndent: bm.indent,
          startOnNewline: !key || key.type === "block-scalar"
        });
        offset = valueProps.end;
        if (valueProps.found) {
          if (implicitKey) {
            if (value?.type === "block-map" && !valueProps.hasNewline)
              onError(offset, "BLOCK_AS_IMPLICIT_KEY", "Nested mappings are not allowed in compact mappings");
            if (ctx.options.strict && keyProps.start < valueProps.found.offset - 1024)
              onError(keyNode.range, "KEY_OVER_1024_CHARS", "The : indicator must be at most 1024 chars after the start of an implicit block mapping key");
          }
          const valueNode = value ? composeNode(ctx, value, valueProps, onError) : composeEmptyNode(ctx, offset, sep, null, valueProps, onError);
          if (ctx.schema.compat)
            utilFlowIndentCheck.flowIndentCheck(bm.indent, value, onError);
          offset = valueNode.range[2];
          const pair = new Pair.Pair(keyNode, valueNode);
          if (ctx.options.keepSourceTokens)
            pair.srcToken = collItem;
          map.items.push(pair);
        } else {
          if (implicitKey)
            onError(keyNode.range, "MISSING_CHAR", "Implicit map keys need to be followed by map values");
          if (valueProps.comment) {
            if (keyNode.comment)
              keyNode.comment += "\n" + valueProps.comment;
            else
              keyNode.comment = valueProps.comment;
          }
          const pair = new Pair.Pair(keyNode);
          if (ctx.options.keepSourceTokens)
            pair.srcToken = collItem;
          map.items.push(pair);
        }
      }
      if (commentEnd && commentEnd < offset)
        onError(commentEnd, "IMPOSSIBLE", "Map comment with trailing content");
      map.range = [bm.offset, offset, commentEnd ?? offset];
      return map;
    }
    exports.resolveBlockMap = resolveBlockMap;
  }
});

// node_modules/yaml/dist/compose/resolve-block-seq.js
var require_resolve_block_seq = __commonJS({
  "node_modules/yaml/dist/compose/resolve-block-seq.js"(exports) {
    "use strict";
    var YAMLSeq = require_YAMLSeq();
    var resolveProps = require_resolve_props();
    var utilFlowIndentCheck = require_util_flow_indent_check();
    function resolveBlockSeq({ composeNode, composeEmptyNode }, ctx, bs, onError, tag) {
      const NodeClass = tag?.nodeClass ?? YAMLSeq.YAMLSeq;
      const seq = new NodeClass(ctx.schema);
      if (ctx.atRoot)
        ctx.atRoot = false;
      if (ctx.atKey)
        ctx.atKey = false;
      let offset = bs.offset;
      let commentEnd = null;
      for (const { start, value } of bs.items) {
        const props = resolveProps.resolveProps(start, {
          indicator: "seq-item-ind",
          next: value,
          offset,
          onError,
          parentIndent: bs.indent,
          startOnNewline: true
        });
        if (!props.found) {
          if (props.anchor || props.tag || value) {
            if (value?.type === "block-seq")
              onError(props.end, "BAD_INDENT", "All sequence items must start at the same column");
            else
              onError(offset, "MISSING_CHAR", "Sequence item without - indicator");
          } else {
            commentEnd = props.end;
            if (props.comment)
              seq.comment = props.comment;
            continue;
          }
        }
        const node = value ? composeNode(ctx, value, props, onError) : composeEmptyNode(ctx, props.end, start, null, props, onError);
        if (ctx.schema.compat)
          utilFlowIndentCheck.flowIndentCheck(bs.indent, value, onError);
        offset = node.range[2];
        seq.items.push(node);
      }
      seq.range = [bs.offset, offset, commentEnd ?? offset];
      return seq;
    }
    exports.resolveBlockSeq = resolveBlockSeq;
  }
});

// node_modules/yaml/dist/compose/resolve-end.js
var require_resolve_end = __commonJS({
  "node_modules/yaml/dist/compose/resolve-end.js"(exports) {
    "use strict";
    function resolveEnd(end, offset, reqSpace, onError) {
      let comment = "";
      if (end) {
        let hasSpace = false;
        let sep = "";
        for (const token of end) {
          const { source, type } = token;
          switch (type) {
            case "space":
              hasSpace = true;
              break;
            case "comment": {
              if (reqSpace && !hasSpace)
                onError(token, "MISSING_CHAR", "Comments must be separated from other tokens by white space characters");
              const cb = source.substring(1) || " ";
              if (!comment)
                comment = cb;
              else
                comment += sep + cb;
              sep = "";
              break;
            }
            case "newline":
              if (comment)
                sep += source;
              hasSpace = true;
              break;
            default:
              onError(token, "UNEXPECTED_TOKEN", `Unexpected ${type} at node end`);
          }
          offset += source.length;
        }
      }
      return { comment, offset };
    }
    exports.resolveEnd = resolveEnd;
  }
});

// node_modules/yaml/dist/compose/resolve-flow-collection.js
var require_resolve_flow_collection = __commonJS({
  "node_modules/yaml/dist/compose/resolve-flow-collection.js"(exports) {
    "use strict";
    var identity = require_identity();
    var Pair = require_Pair();
    var YAMLMap = require_YAMLMap();
    var YAMLSeq = require_YAMLSeq();
    var resolveEnd = require_resolve_end();
    var resolveProps = require_resolve_props();
    var utilContainsNewline = require_util_contains_newline();
    var utilMapIncludes = require_util_map_includes();
    var blockMsg = "Block collections are not allowed within flow collections";
    var isBlock = (token) => token && (token.type === "block-map" || token.type === "block-seq");
    function resolveFlowCollection({ composeNode, composeEmptyNode }, ctx, fc, onError, tag) {
      const isMap = fc.start.source === "{";
      const fcName = isMap ? "flow map" : "flow sequence";
      const NodeClass = tag?.nodeClass ?? (isMap ? YAMLMap.YAMLMap : YAMLSeq.YAMLSeq);
      const coll = new NodeClass(ctx.schema);
      coll.flow = true;
      const atRoot = ctx.atRoot;
      if (atRoot)
        ctx.atRoot = false;
      if (ctx.atKey)
        ctx.atKey = false;
      let offset = fc.offset + fc.start.source.length;
      for (let i = 0; i < fc.items.length; ++i) {
        const collItem = fc.items[i];
        const { start, key, sep, value } = collItem;
        const props = resolveProps.resolveProps(start, {
          flow: fcName,
          indicator: "explicit-key-ind",
          next: key ?? sep?.[0],
          offset,
          onError,
          parentIndent: fc.indent,
          startOnNewline: false
        });
        if (!props.found) {
          if (!props.anchor && !props.tag && !sep && !value) {
            if (i === 0 && props.comma)
              onError(props.comma, "UNEXPECTED_TOKEN", `Unexpected , in ${fcName}`);
            else if (i < fc.items.length - 1)
              onError(props.start, "UNEXPECTED_TOKEN", `Unexpected empty item in ${fcName}`);
            if (props.comment) {
              if (coll.comment)
                coll.comment += "\n" + props.comment;
              else
                coll.comment = props.comment;
            }
            offset = props.end;
            continue;
          }
          if (!isMap && ctx.options.strict && utilContainsNewline.containsNewline(key))
            onError(
              key,
              // checked by containsNewline()
              "MULTILINE_IMPLICIT_KEY",
              "Implicit keys of flow sequence pairs need to be on a single line"
            );
        }
        if (i === 0) {
          if (props.comma)
            onError(props.comma, "UNEXPECTED_TOKEN", `Unexpected , in ${fcName}`);
        } else {
          if (!props.comma)
            onError(props.start, "MISSING_CHAR", `Missing , between ${fcName} items`);
          if (props.comment) {
            let prevItemComment = "";
            loop: for (const st of start) {
              switch (st.type) {
                case "comma":
                case "space":
                  break;
                case "comment":
                  prevItemComment = st.source.substring(1);
                  break loop;
                default:
                  break loop;
              }
            }
            if (prevItemComment) {
              let prev = coll.items[coll.items.length - 1];
              if (identity.isPair(prev))
                prev = prev.value ?? prev.key;
              if (prev.comment)
                prev.comment += "\n" + prevItemComment;
              else
                prev.comment = prevItemComment;
              props.comment = props.comment.substring(prevItemComment.length + 1);
            }
          }
        }
        if (!isMap && !sep && !props.found) {
          const valueNode = value ? composeNode(ctx, value, props, onError) : composeEmptyNode(ctx, props.end, sep, null, props, onError);
          coll.items.push(valueNode);
          offset = valueNode.range[2];
          if (isBlock(value))
            onError(valueNode.range, "BLOCK_IN_FLOW", blockMsg);
        } else {
          ctx.atKey = true;
          const keyStart = props.end;
          const keyNode = key ? composeNode(ctx, key, props, onError) : composeEmptyNode(ctx, keyStart, start, null, props, onError);
          if (isBlock(key))
            onError(keyNode.range, "BLOCK_IN_FLOW", blockMsg);
          ctx.atKey = false;
          const valueProps = resolveProps.resolveProps(sep ?? [], {
            flow: fcName,
            indicator: "map-value-ind",
            next: value,
            offset: keyNode.range[2],
            onError,
            parentIndent: fc.indent,
            startOnNewline: false
          });
          if (valueProps.found) {
            if (!isMap && !props.found && ctx.options.strict) {
              if (sep)
                for (const st of sep) {
                  if (st === valueProps.found)
                    break;
                  if (st.type === "newline") {
                    onError(st, "MULTILINE_IMPLICIT_KEY", "Implicit keys of flow sequence pairs need to be on a single line");
                    break;
                  }
                }
              if (props.start < valueProps.found.offset - 1024)
                onError(valueProps.found, "KEY_OVER_1024_CHARS", "The : indicator must be at most 1024 chars after the start of an implicit flow sequence key");
            }
          } else if (value) {
            if ("source" in value && value.source?.[0] === ":")
              onError(value, "MISSING_CHAR", `Missing space after : in ${fcName}`);
            else
              onError(valueProps.start, "MISSING_CHAR", `Missing , or : between ${fcName} items`);
          }
          const valueNode = value ? composeNode(ctx, value, valueProps, onError) : valueProps.found ? composeEmptyNode(ctx, valueProps.end, sep, null, valueProps, onError) : null;
          if (valueNode) {
            if (isBlock(value))
              onError(valueNode.range, "BLOCK_IN_FLOW", blockMsg);
          } else if (valueProps.comment) {
            if (keyNode.comment)
              keyNode.comment += "\n" + valueProps.comment;
            else
              keyNode.comment = valueProps.comment;
          }
          const pair = new Pair.Pair(keyNode, valueNode);
          if (ctx.options.keepSourceTokens)
            pair.srcToken = collItem;
          if (isMap) {
            const map = coll;
            if (utilMapIncludes.mapIncludes(ctx, map.items, keyNode))
              onError(keyStart, "DUPLICATE_KEY", "Map keys must be unique");
            map.items.push(pair);
          } else {
            const map = new YAMLMap.YAMLMap(ctx.schema);
            map.flow = true;
            map.items.push(pair);
            const endRange = (valueNode ?? keyNode).range;
            map.range = [keyNode.range[0], endRange[1], endRange[2]];
            coll.items.push(map);
          }
          offset = valueNode ? valueNode.range[2] : valueProps.end;
        }
      }
      const expectedEnd = isMap ? "}" : "]";
      const [ce, ...ee] = fc.end;
      let cePos = offset;
      if (ce?.source === expectedEnd)
        cePos = ce.offset + ce.source.length;
      else {
        const name = fcName[0].toUpperCase() + fcName.substring(1);
        const msg = atRoot ? `${name} must end with a ${expectedEnd}` : `${name} in block collection must be sufficiently indented and end with a ${expectedEnd}`;
        onError(offset, atRoot ? "MISSING_CHAR" : "BAD_INDENT", msg);
        if (ce && ce.source.length !== 1)
          ee.unshift(ce);
      }
      if (ee.length > 0) {
        const end = resolveEnd.resolveEnd(ee, cePos, ctx.options.strict, onError);
        if (end.comment) {
          if (coll.comment)
            coll.comment += "\n" + end.comment;
          else
            coll.comment = end.comment;
        }
        coll.range = [fc.offset, cePos, end.offset];
      } else {
        coll.range = [fc.offset, cePos, cePos];
      }
      return coll;
    }
    exports.resolveFlowCollection = resolveFlowCollection;
  }
});

// node_modules/yaml/dist/compose/compose-collection.js
var require_compose_collection = __commonJS({
  "node_modules/yaml/dist/compose/compose-collection.js"(exports) {
    "use strict";
    var identity = require_identity();
    var Scalar = require_Scalar();
    var YAMLMap = require_YAMLMap();
    var YAMLSeq = require_YAMLSeq();
    var resolveBlockMap = require_resolve_block_map();
    var resolveBlockSeq = require_resolve_block_seq();
    var resolveFlowCollection = require_resolve_flow_collection();
    function resolveCollection(CN, ctx, token, onError, tagName, tag) {
      const coll = token.type === "block-map" ? resolveBlockMap.resolveBlockMap(CN, ctx, token, onError, tag) : token.type === "block-seq" ? resolveBlockSeq.resolveBlockSeq(CN, ctx, token, onError, tag) : resolveFlowCollection.resolveFlowCollection(CN, ctx, token, onError, tag);
      const Coll = coll.constructor;
      if (tagName === "!" || tagName === Coll.tagName) {
        coll.tag = Coll.tagName;
        return coll;
      }
      if (tagName)
        coll.tag = tagName;
      return coll;
    }
    function composeCollection(CN, ctx, token, props, onError) {
      const tagToken = props.tag;
      const tagName = !tagToken ? null : ctx.directives.tagName(tagToken.source, (msg) => onError(tagToken, "TAG_RESOLVE_FAILED", msg));
      if (token.type === "block-seq") {
        const { anchor, newlineAfterProp: nl } = props;
        const lastProp = anchor && tagToken ? anchor.offset > tagToken.offset ? anchor : tagToken : anchor ?? tagToken;
        if (lastProp && (!nl || nl.offset < lastProp.offset)) {
          const message = "Missing newline after block sequence props";
          onError(lastProp, "MISSING_CHAR", message);
        }
      }
      const expType = token.type === "block-map" ? "map" : token.type === "block-seq" ? "seq" : token.start.source === "{" ? "map" : "seq";
      if (!tagToken || !tagName || tagName === "!" || tagName === YAMLMap.YAMLMap.tagName && expType === "map" || tagName === YAMLSeq.YAMLSeq.tagName && expType === "seq") {
        return resolveCollection(CN, ctx, token, onError, tagName);
      }
      let tag = ctx.schema.tags.find((t) => t.tag === tagName && t.collection === expType);
      if (!tag) {
        const kt = ctx.schema.knownTags[tagName];
        if (kt?.collection === expType) {
          ctx.schema.tags.push(Object.assign({}, kt, { default: false }));
          tag = kt;
        } else {
          if (kt) {
            onError(tagToken, "BAD_COLLECTION_TYPE", `${kt.tag} used for ${expType} collection, but expects ${kt.collection ?? "scalar"}`, true);
          } else {
            onError(tagToken, "TAG_RESOLVE_FAILED", `Unresolved tag: ${tagName}`, true);
          }
          return resolveCollection(CN, ctx, token, onError, tagName);
        }
      }
      const coll = resolveCollection(CN, ctx, token, onError, tagName, tag);
      const res = tag.resolve?.(coll, (msg) => onError(tagToken, "TAG_RESOLVE_FAILED", msg), ctx.options) ?? coll;
      const node = identity.isNode(res) ? res : new Scalar.Scalar(res);
      node.range = coll.range;
      node.tag = tagName;
      if (tag?.format)
        node.format = tag.format;
      return node;
    }
    exports.composeCollection = composeCollection;
  }
});

// node_modules/yaml/dist/compose/resolve-block-scalar.js
var require_resolve_block_scalar = __commonJS({
  "node_modules/yaml/dist/compose/resolve-block-scalar.js"(exports) {
    "use strict";
    var Scalar = require_Scalar();
    function resolveBlockScalar(ctx, scalar, onError) {
      const start = scalar.offset;
      const header = parseBlockScalarHeader(scalar, ctx.options.strict, onError);
      if (!header)
        return { value: "", type: null, comment: "", range: [start, start, start] };
      const type = header.mode === ">" ? Scalar.Scalar.BLOCK_FOLDED : Scalar.Scalar.BLOCK_LITERAL;
      const lines = scalar.source ? splitLines(scalar.source) : [];
      let chompStart = lines.length;
      for (let i = lines.length - 1; i >= 0; --i) {
        const content2 = lines[i][1];
        if (content2 === "" || content2 === "\r")
          chompStart = i;
        else
          break;
      }
      if (chompStart === 0) {
        const value2 = header.chomp === "+" && lines.length > 0 ? "\n".repeat(Math.max(1, lines.length - 1)) : "";
        let end2 = start + header.length;
        if (scalar.source)
          end2 += scalar.source.length;
        return { value: value2, type, comment: header.comment, range: [start, end2, end2] };
      }
      let trimIndent = scalar.indent + header.indent;
      let offset = scalar.offset + header.length;
      let contentStart = 0;
      for (let i = 0; i < chompStart; ++i) {
        const [indent, content2] = lines[i];
        if (content2 === "" || content2 === "\r") {
          if (header.indent === 0 && indent.length > trimIndent)
            trimIndent = indent.length;
        } else {
          if (indent.length < trimIndent) {
            const message = "Block scalars with more-indented leading empty lines must use an explicit indentation indicator";
            onError(offset + indent.length, "MISSING_CHAR", message);
          }
          if (header.indent === 0)
            trimIndent = indent.length;
          contentStart = i;
          if (trimIndent === 0 && !ctx.atRoot) {
            const message = "Block scalar values in collections must be indented";
            onError(offset, "BAD_INDENT", message);
          }
          break;
        }
        offset += indent.length + content2.length + 1;
      }
      for (let i = lines.length - 1; i >= chompStart; --i) {
        if (lines[i][0].length > trimIndent)
          chompStart = i + 1;
      }
      let value = "";
      let sep = "";
      let prevMoreIndented = false;
      for (let i = 0; i < contentStart; ++i)
        value += lines[i][0].slice(trimIndent) + "\n";
      for (let i = contentStart; i < chompStart; ++i) {
        let [indent, content2] = lines[i];
        offset += indent.length + content2.length + 1;
        const crlf = content2[content2.length - 1] === "\r";
        if (crlf)
          content2 = content2.slice(0, -1);
        if (content2 && indent.length < trimIndent) {
          const src = header.indent ? "explicit indentation indicator" : "first line";
          const message = `Block scalar lines must not be less indented than their ${src}`;
          onError(offset - content2.length - (crlf ? 2 : 1), "BAD_INDENT", message);
          indent = "";
        }
        if (type === Scalar.Scalar.BLOCK_LITERAL) {
          value += sep + indent.slice(trimIndent) + content2;
          sep = "\n";
        } else if (indent.length > trimIndent || content2[0] === "	") {
          if (sep === " ")
            sep = "\n";
          else if (!prevMoreIndented && sep === "\n")
            sep = "\n\n";
          value += sep + indent.slice(trimIndent) + content2;
          sep = "\n";
          prevMoreIndented = true;
        } else if (content2 === "") {
          if (sep === "\n")
            value += "\n";
          else
            sep = "\n";
        } else {
          value += sep + content2;
          sep = " ";
          prevMoreIndented = false;
        }
      }
      switch (header.chomp) {
        case "-":
          break;
        case "+":
          for (let i = chompStart; i < lines.length; ++i)
            value += "\n" + lines[i][0].slice(trimIndent);
          if (value[value.length - 1] !== "\n")
            value += "\n";
          break;
        default:
          value += "\n";
      }
      const end = start + header.length + scalar.source.length;
      return { value, type, comment: header.comment, range: [start, end, end] };
    }
    function parseBlockScalarHeader({ offset, props }, strict, onError) {
      if (props[0].type !== "block-scalar-header") {
        onError(props[0], "IMPOSSIBLE", "Block scalar header not found");
        return null;
      }
      const { source } = props[0];
      const mode = source[0];
      let indent = 0;
      let chomp = "";
      let error = -1;
      for (let i = 1; i < source.length; ++i) {
        const ch = source[i];
        if (!chomp && (ch === "-" || ch === "+"))
          chomp = ch;
        else {
          const n = Number(ch);
          if (!indent && n)
            indent = n;
          else if (error === -1)
            error = offset + i;
        }
      }
      if (error !== -1)
        onError(error, "UNEXPECTED_TOKEN", `Block scalar header includes extra characters: ${source}`);
      let hasSpace = false;
      let comment = "";
      let length = source.length;
      for (let i = 1; i < props.length; ++i) {
        const token = props[i];
        switch (token.type) {
          case "space":
            hasSpace = true;
          // fallthrough
          case "newline":
            length += token.source.length;
            break;
          case "comment":
            if (strict && !hasSpace) {
              const message = "Comments must be separated from other tokens by white space characters";
              onError(token, "MISSING_CHAR", message);
            }
            length += token.source.length;
            comment = token.source.substring(1);
            break;
          case "error":
            onError(token, "UNEXPECTED_TOKEN", token.message);
            length += token.source.length;
            break;
          /* istanbul ignore next should not happen */
          default: {
            const message = `Unexpected token in block scalar header: ${token.type}`;
            onError(token, "UNEXPECTED_TOKEN", message);
            const ts = token.source;
            if (ts && typeof ts === "string")
              length += ts.length;
          }
        }
      }
      return { mode, indent, chomp, comment, length };
    }
    function splitLines(source) {
      const split = source.split(/\n( *)/);
      const first = split[0];
      const m = first.match(/^( *)/);
      const line0 = m?.[1] ? [m[1], first.slice(m[1].length)] : ["", first];
      const lines = [line0];
      for (let i = 1; i < split.length; i += 2)
        lines.push([split[i], split[i + 1]]);
      return lines;
    }
    exports.resolveBlockScalar = resolveBlockScalar;
  }
});

// node_modules/yaml/dist/compose/resolve-flow-scalar.js
var require_resolve_flow_scalar = __commonJS({
  "node_modules/yaml/dist/compose/resolve-flow-scalar.js"(exports) {
    "use strict";
    var Scalar = require_Scalar();
    var resolveEnd = require_resolve_end();
    function resolveFlowScalar(scalar, strict, onError) {
      const { offset, type, source, end } = scalar;
      let _type;
      let value;
      const _onError = (rel, code, msg) => onError(offset + rel, code, msg);
      switch (type) {
        case "scalar":
          _type = Scalar.Scalar.PLAIN;
          value = plainValue(source, _onError);
          break;
        case "single-quoted-scalar":
          _type = Scalar.Scalar.QUOTE_SINGLE;
          value = singleQuotedValue(source, _onError);
          break;
        case "double-quoted-scalar":
          _type = Scalar.Scalar.QUOTE_DOUBLE;
          value = doubleQuotedValue(source, _onError);
          break;
        /* istanbul ignore next should not happen */
        default:
          onError(scalar, "UNEXPECTED_TOKEN", `Expected a flow scalar value, but found: ${type}`);
          return {
            value: "",
            type: null,
            comment: "",
            range: [offset, offset + source.length, offset + source.length]
          };
      }
      const valueEnd = offset + source.length;
      const re = resolveEnd.resolveEnd(end, valueEnd, strict, onError);
      return {
        value,
        type: _type,
        comment: re.comment,
        range: [offset, valueEnd, re.offset]
      };
    }
    function plainValue(source, onError) {
      let badChar = "";
      switch (source[0]) {
        /* istanbul ignore next should not happen */
        case "	":
          badChar = "a tab character";
          break;
        case ",":
          badChar = "flow indicator character ,";
          break;
        case "%":
          badChar = "directive indicator character %";
          break;
        case "|":
        case ">": {
          badChar = `block scalar indicator ${source[0]}`;
          break;
        }
        case "@":
        case "`": {
          badChar = `reserved character ${source[0]}`;
          break;
        }
      }
      if (badChar)
        onError(0, "BAD_SCALAR_START", `Plain value cannot start with ${badChar}`);
      return foldLines(source);
    }
    function singleQuotedValue(source, onError) {
      if (source[source.length - 1] !== "'" || source.length === 1)
        onError(source.length, "MISSING_CHAR", "Missing closing 'quote");
      return foldLines(source.slice(1, -1)).replace(/''/g, "'");
    }
    function foldLines(source) {
      let first, line;
      try {
        first = new RegExp("(.*?)(?<![ 	])[ 	]*\r?\n", "sy");
        line = new RegExp("[ 	]*(.*?)(?:(?<![ 	])[ 	]*)?\r?\n", "sy");
      } catch {
        first = /(.*?)[ \t]*\r?\n/sy;
        line = /[ \t]*(.*?)[ \t]*\r?\n/sy;
      }
      let match = first.exec(source);
      if (!match)
        return source;
      let res = match[1];
      let sep = " ";
      let pos = first.lastIndex;
      line.lastIndex = pos;
      while (match = line.exec(source)) {
        if (match[1] === "") {
          if (sep === "\n")
            res += sep;
          else
            sep = "\n";
        } else {
          res += sep + match[1];
          sep = " ";
        }
        pos = line.lastIndex;
      }
      const last = /[ \t]*(.*)/sy;
      last.lastIndex = pos;
      match = last.exec(source);
      return res + sep + (match?.[1] ?? "");
    }
    function doubleQuotedValue(source, onError) {
      let res = "";
      for (let i = 1; i < source.length - 1; ++i) {
        const ch = source[i];
        if (ch === "\r" && source[i + 1] === "\n")
          continue;
        if (ch === "\n") {
          const { fold, offset } = foldNewline(source, i);
          res += fold;
          i = offset;
        } else if (ch === "\\") {
          let next = source[++i];
          const cc = escapeCodes[next];
          if (cc)
            res += cc;
          else if (next === "\n") {
            next = source[i + 1];
            while (next === " " || next === "	")
              next = source[++i + 1];
          } else if (next === "\r" && source[i + 1] === "\n") {
            next = source[++i + 1];
            while (next === " " || next === "	")
              next = source[++i + 1];
          } else if (next === "x" || next === "u" || next === "U") {
            const length = next === "x" ? 2 : next === "u" ? 4 : 8;
            res += parseCharCode(source, i + 1, length, onError);
            i += length;
          } else {
            const raw = source.substr(i - 1, 2);
            onError(i - 1, "BAD_DQ_ESCAPE", `Invalid escape sequence ${raw}`);
            res += raw;
          }
        } else if (ch === " " || ch === "	") {
          const wsStart = i;
          let next = source[i + 1];
          while (next === " " || next === "	")
            next = source[++i + 1];
          if (next !== "\n" && !(next === "\r" && source[i + 2] === "\n"))
            res += i > wsStart ? source.slice(wsStart, i + 1) : ch;
        } else {
          res += ch;
        }
      }
      if (source[source.length - 1] !== '"' || source.length === 1)
        onError(source.length, "MISSING_CHAR", 'Missing closing "quote');
      return res;
    }
    function foldNewline(source, offset) {
      let fold = "";
      let ch = source[offset + 1];
      while (ch === " " || ch === "	" || ch === "\n" || ch === "\r") {
        if (ch === "\r" && source[offset + 2] !== "\n")
          break;
        if (ch === "\n")
          fold += "\n";
        offset += 1;
        ch = source[offset + 1];
      }
      if (!fold)
        fold = " ";
      return { fold, offset };
    }
    var escapeCodes = {
      "0": "\0",
      // null character
      a: "\x07",
      // bell character
      b: "\b",
      // backspace
      e: "\x1B",
      // escape character
      f: "\f",
      // form feed
      n: "\n",
      // line feed
      r: "\r",
      // carriage return
      t: "	",
      // horizontal tab
      v: "\v",
      // vertical tab
      N: "\x85",
      // Unicode next line
      _: "\xA0",
      // Unicode non-breaking space
      L: "\u2028",
      // Unicode line separator
      P: "\u2029",
      // Unicode paragraph separator
      " ": " ",
      '"': '"',
      "/": "/",
      "\\": "\\",
      "	": "	"
    };
    function parseCharCode(source, offset, length, onError) {
      const cc = source.substr(offset, length);
      const ok = cc.length === length && /^[0-9a-fA-F]+$/.test(cc);
      const code = ok ? parseInt(cc, 16) : NaN;
      try {
        return String.fromCodePoint(code);
      } catch {
        const raw = source.substr(offset - 2, length + 2);
        onError(offset - 2, "BAD_DQ_ESCAPE", `Invalid escape sequence ${raw}`);
        return raw;
      }
    }
    exports.resolveFlowScalar = resolveFlowScalar;
  }
});

// node_modules/yaml/dist/compose/compose-scalar.js
var require_compose_scalar = __commonJS({
  "node_modules/yaml/dist/compose/compose-scalar.js"(exports) {
    "use strict";
    var identity = require_identity();
    var Scalar = require_Scalar();
    var resolveBlockScalar = require_resolve_block_scalar();
    var resolveFlowScalar = require_resolve_flow_scalar();
    function composeScalar(ctx, token, tagToken, onError) {
      const { value, type, comment, range } = token.type === "block-scalar" ? resolveBlockScalar.resolveBlockScalar(ctx, token, onError) : resolveFlowScalar.resolveFlowScalar(token, ctx.options.strict, onError);
      const tagName = tagToken ? ctx.directives.tagName(tagToken.source, (msg) => onError(tagToken, "TAG_RESOLVE_FAILED", msg)) : null;
      let tag;
      if (ctx.options.stringKeys && ctx.atKey) {
        tag = ctx.schema[identity.SCALAR];
      } else if (tagName)
        tag = findScalarTagByName(ctx.schema, value, tagName, tagToken, onError);
      else if (token.type === "scalar")
        tag = findScalarTagByTest(ctx, value, token, onError);
      else
        tag = ctx.schema[identity.SCALAR];
      let scalar;
      try {
        const res = tag.resolve(value, (msg) => onError(tagToken ?? token, "TAG_RESOLVE_FAILED", msg), ctx.options);
        scalar = identity.isScalar(res) ? res : new Scalar.Scalar(res);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        onError(tagToken ?? token, "TAG_RESOLVE_FAILED", msg);
        scalar = new Scalar.Scalar(value);
      }
      scalar.range = range;
      scalar.source = value;
      if (type)
        scalar.type = type;
      if (tagName)
        scalar.tag = tagName;
      if (tag.format)
        scalar.format = tag.format;
      if (comment)
        scalar.comment = comment;
      return scalar;
    }
    function findScalarTagByName(schema, value, tagName, tagToken, onError) {
      if (tagName === "!")
        return schema[identity.SCALAR];
      const matchWithTest = [];
      for (const tag of schema.tags) {
        if (!tag.collection && tag.tag === tagName) {
          if (tag.default && tag.test)
            matchWithTest.push(tag);
          else
            return tag;
        }
      }
      for (const tag of matchWithTest)
        if (tag.test?.test(value))
          return tag;
      const kt = schema.knownTags[tagName];
      if (kt && !kt.collection) {
        schema.tags.push(Object.assign({}, kt, { default: false, test: void 0 }));
        return kt;
      }
      onError(tagToken, "TAG_RESOLVE_FAILED", `Unresolved tag: ${tagName}`, tagName !== "tag:yaml.org,2002:str");
      return schema[identity.SCALAR];
    }
    function findScalarTagByTest({ atKey, directives, schema }, value, token, onError) {
      const tag = schema.tags.find((tag2) => (tag2.default === true || atKey && tag2.default === "key") && tag2.test?.test(value)) || schema[identity.SCALAR];
      if (schema.compat) {
        const compat = schema.compat.find((tag2) => tag2.default && tag2.test?.test(value)) ?? schema[identity.SCALAR];
        if (tag.tag !== compat.tag) {
          const ts = directives.tagString(tag.tag);
          const cs = directives.tagString(compat.tag);
          const msg = `Value may be parsed as either ${ts} or ${cs}`;
          onError(token, "TAG_RESOLVE_FAILED", msg, true);
        }
      }
      return tag;
    }
    exports.composeScalar = composeScalar;
  }
});

// node_modules/yaml/dist/compose/util-empty-scalar-position.js
var require_util_empty_scalar_position = __commonJS({
  "node_modules/yaml/dist/compose/util-empty-scalar-position.js"(exports) {
    "use strict";
    function emptyScalarPosition(offset, before, pos) {
      if (before) {
        pos ?? (pos = before.length);
        for (let i = pos - 1; i >= 0; --i) {
          let st = before[i];
          switch (st.type) {
            case "space":
            case "comment":
            case "newline":
              offset -= st.source.length;
              continue;
          }
          st = before[++i];
          while (st?.type === "space") {
            offset += st.source.length;
            st = before[++i];
          }
          break;
        }
      }
      return offset;
    }
    exports.emptyScalarPosition = emptyScalarPosition;
  }
});

// node_modules/yaml/dist/compose/compose-node.js
var require_compose_node = __commonJS({
  "node_modules/yaml/dist/compose/compose-node.js"(exports) {
    "use strict";
    var Alias = require_Alias();
    var identity = require_identity();
    var composeCollection = require_compose_collection();
    var composeScalar = require_compose_scalar();
    var resolveEnd = require_resolve_end();
    var utilEmptyScalarPosition = require_util_empty_scalar_position();
    var CN = { composeNode, composeEmptyNode };
    function composeNode(ctx, token, props, onError) {
      const atKey = ctx.atKey;
      const { spaceBefore, comment, anchor, tag } = props;
      let node;
      let isSrcToken = true;
      switch (token.type) {
        case "alias":
          node = composeAlias(ctx, token, onError);
          if (anchor || tag)
            onError(token, "ALIAS_PROPS", "An alias node must not specify any properties");
          break;
        case "scalar":
        case "single-quoted-scalar":
        case "double-quoted-scalar":
        case "block-scalar":
          node = composeScalar.composeScalar(ctx, token, tag, onError);
          if (anchor)
            node.anchor = anchor.source.substring(1);
          break;
        case "block-map":
        case "block-seq":
        case "flow-collection":
          try {
            node = composeCollection.composeCollection(CN, ctx, token, props, onError);
            if (anchor)
              node.anchor = anchor.source.substring(1);
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            onError(token, "RESOURCE_EXHAUSTION", message);
          }
          break;
        default: {
          const message = token.type === "error" ? token.message : `Unsupported token (type: ${token.type})`;
          onError(token, "UNEXPECTED_TOKEN", message);
          isSrcToken = false;
        }
      }
      node ?? (node = composeEmptyNode(ctx, token.offset, void 0, null, props, onError));
      if (anchor && node.anchor === "")
        onError(anchor, "BAD_ALIAS", "Anchor cannot be an empty string");
      if (atKey && ctx.options.stringKeys && (!identity.isScalar(node) || typeof node.value !== "string" || node.tag && node.tag !== "tag:yaml.org,2002:str")) {
        const msg = "With stringKeys, all keys must be strings";
        onError(tag ?? token, "NON_STRING_KEY", msg);
      }
      if (spaceBefore)
        node.spaceBefore = true;
      if (comment) {
        if (token.type === "scalar" && token.source === "")
          node.comment = comment;
        else
          node.commentBefore = comment;
      }
      if (ctx.options.keepSourceTokens && isSrcToken)
        node.srcToken = token;
      return node;
    }
    function composeEmptyNode(ctx, offset, before, pos, { spaceBefore, comment, anchor, tag, end }, onError) {
      const token = {
        type: "scalar",
        offset: utilEmptyScalarPosition.emptyScalarPosition(offset, before, pos),
        indent: -1,
        source: ""
      };
      const node = composeScalar.composeScalar(ctx, token, tag, onError);
      if (anchor) {
        node.anchor = anchor.source.substring(1);
        if (node.anchor === "")
          onError(anchor, "BAD_ALIAS", "Anchor cannot be an empty string");
      }
      if (spaceBefore)
        node.spaceBefore = true;
      if (comment) {
        node.comment = comment;
        node.range[2] = end;
      }
      return node;
    }
    function composeAlias({ options }, { offset, source, end }, onError) {
      const alias = new Alias.Alias(source.substring(1));
      if (alias.source === "")
        onError(offset, "BAD_ALIAS", "Alias cannot be an empty string");
      if (alias.source.endsWith(":"))
        onError(offset + source.length - 1, "BAD_ALIAS", "Alias ending in : is ambiguous", true);
      const valueEnd = offset + source.length;
      const re = resolveEnd.resolveEnd(end, valueEnd, options.strict, onError);
      alias.range = [offset, valueEnd, re.offset];
      if (re.comment)
        alias.comment = re.comment;
      return alias;
    }
    exports.composeEmptyNode = composeEmptyNode;
    exports.composeNode = composeNode;
  }
});

// node_modules/yaml/dist/compose/compose-doc.js
var require_compose_doc = __commonJS({
  "node_modules/yaml/dist/compose/compose-doc.js"(exports) {
    "use strict";
    var Document = require_Document();
    var composeNode = require_compose_node();
    var resolveEnd = require_resolve_end();
    var resolveProps = require_resolve_props();
    function composeDoc(options, directives, { offset, start, value, end }, onError) {
      const opts = Object.assign({ _directives: directives }, options);
      const doc = new Document.Document(void 0, opts);
      const ctx = {
        atKey: false,
        atRoot: true,
        directives: doc.directives,
        options: doc.options,
        schema: doc.schema
      };
      const props = resolveProps.resolveProps(start, {
        indicator: "doc-start",
        next: value ?? end?.[0],
        offset,
        onError,
        parentIndent: 0,
        startOnNewline: true
      });
      if (props.found) {
        doc.directives.docStart = true;
        if (value && (value.type === "block-map" || value.type === "block-seq") && !props.hasNewline)
          onError(props.end, "MISSING_CHAR", "Block collection cannot start on same line with directives-end marker");
      }
      doc.contents = value ? composeNode.composeNode(ctx, value, props, onError) : composeNode.composeEmptyNode(ctx, props.end, start, null, props, onError);
      const contentEnd = doc.contents.range[2];
      const re = resolveEnd.resolveEnd(end, contentEnd, false, onError);
      if (re.comment)
        doc.comment = re.comment;
      doc.range = [offset, contentEnd, re.offset];
      return doc;
    }
    exports.composeDoc = composeDoc;
  }
});

// node_modules/yaml/dist/compose/composer.js
var require_composer = __commonJS({
  "node_modules/yaml/dist/compose/composer.js"(exports) {
    "use strict";
    var node_process = __require("process");
    var directives = require_directives();
    var Document = require_Document();
    var errors = require_errors();
    var identity = require_identity();
    var composeDoc = require_compose_doc();
    var resolveEnd = require_resolve_end();
    function getErrorPos(src) {
      if (typeof src === "number")
        return [src, src + 1];
      if (Array.isArray(src))
        return src.length === 2 ? src : [src[0], src[1]];
      const { offset, source } = src;
      return [offset, offset + (typeof source === "string" ? source.length : 1)];
    }
    function parsePrelude(prelude) {
      let comment = "";
      let atComment = false;
      let afterEmptyLine = false;
      for (let i = 0; i < prelude.length; ++i) {
        const source = prelude[i];
        switch (source[0]) {
          case "#":
            comment += (comment === "" ? "" : afterEmptyLine ? "\n\n" : "\n") + (source.substring(1) || " ");
            atComment = true;
            afterEmptyLine = false;
            break;
          case "%":
            if (prelude[i + 1]?.[0] !== "#")
              i += 1;
            atComment = false;
            break;
          default:
            if (!atComment)
              afterEmptyLine = true;
            atComment = false;
        }
      }
      return { comment, afterEmptyLine };
    }
    var Composer = class {
      constructor(options = {}) {
        this.doc = null;
        this.atDirectives = false;
        this.prelude = [];
        this.errors = [];
        this.warnings = [];
        this.onError = (source, code, message, warning) => {
          const pos = getErrorPos(source);
          if (warning)
            this.warnings.push(new errors.YAMLWarning(pos, code, message));
          else
            this.errors.push(new errors.YAMLParseError(pos, code, message));
        };
        this.directives = new directives.Directives({ version: options.version || "1.2" });
        this.options = options;
      }
      decorate(doc, afterDoc) {
        const { comment, afterEmptyLine } = parsePrelude(this.prelude);
        if (comment) {
          const dc = doc.contents;
          if (afterDoc) {
            doc.comment = doc.comment ? `${doc.comment}
${comment}` : comment;
          } else if (afterEmptyLine || doc.directives.docStart || !dc) {
            doc.commentBefore = comment;
          } else if (identity.isCollection(dc) && !dc.flow && dc.items.length > 0) {
            let it = dc.items[0];
            if (identity.isPair(it))
              it = it.key;
            const cb = it.commentBefore;
            it.commentBefore = cb ? `${comment}
${cb}` : comment;
          } else {
            const cb = dc.commentBefore;
            dc.commentBefore = cb ? `${comment}
${cb}` : comment;
          }
        }
        if (afterDoc) {
          for (let i = 0; i < this.errors.length; ++i)
            doc.errors.push(this.errors[i]);
          for (let i = 0; i < this.warnings.length; ++i)
            doc.warnings.push(this.warnings[i]);
        } else {
          doc.errors = this.errors;
          doc.warnings = this.warnings;
        }
        this.prelude = [];
        this.errors = [];
        this.warnings = [];
      }
      /**
       * Current stream status information.
       *
       * Mostly useful at the end of input for an empty stream.
       */
      streamInfo() {
        return {
          comment: parsePrelude(this.prelude).comment,
          directives: this.directives,
          errors: this.errors,
          warnings: this.warnings
        };
      }
      /**
       * Compose tokens into documents.
       *
       * @param forceDoc - If the stream contains no document, still emit a final document including any comments and directives that would be applied to a subsequent document.
       * @param endOffset - Should be set if `forceDoc` is also set, to set the document range end and to indicate errors correctly.
       */
      *compose(tokens, forceDoc = false, endOffset = -1) {
        for (const token of tokens)
          yield* this.next(token);
        yield* this.end(forceDoc, endOffset);
      }
      /** Advance the composer by one CST token. */
      *next(token) {
        if (node_process.env.LOG_STREAM)
          console.dir(token, { depth: null });
        switch (token.type) {
          case "directive":
            this.directives.add(token.source, (offset, message, warning) => {
              const pos = getErrorPos(token);
              pos[0] += offset;
              this.onError(pos, "BAD_DIRECTIVE", message, warning);
            });
            this.prelude.push(token.source);
            this.atDirectives = true;
            break;
          case "document": {
            const doc = composeDoc.composeDoc(this.options, this.directives, token, this.onError);
            if (this.atDirectives && !doc.directives.docStart)
              this.onError(token, "MISSING_CHAR", "Missing directives-end/doc-start indicator line");
            this.decorate(doc, false);
            if (this.doc)
              yield this.doc;
            this.doc = doc;
            this.atDirectives = false;
            break;
          }
          case "byte-order-mark":
          case "space":
            break;
          case "comment":
          case "newline":
            this.prelude.push(token.source);
            break;
          case "error": {
            const msg = token.source ? `${token.message}: ${JSON.stringify(token.source)}` : token.message;
            const error = new errors.YAMLParseError(getErrorPos(token), "UNEXPECTED_TOKEN", msg);
            if (this.atDirectives || !this.doc)
              this.errors.push(error);
            else
              this.doc.errors.push(error);
            break;
          }
          case "doc-end": {
            if (!this.doc) {
              const msg = "Unexpected doc-end without preceding document";
              this.errors.push(new errors.YAMLParseError(getErrorPos(token), "UNEXPECTED_TOKEN", msg));
              break;
            }
            this.doc.directives.docEnd = true;
            const end = resolveEnd.resolveEnd(token.end, token.offset + token.source.length, this.doc.options.strict, this.onError);
            this.decorate(this.doc, true);
            if (end.comment) {
              const dc = this.doc.comment;
              this.doc.comment = dc ? `${dc}
${end.comment}` : end.comment;
            }
            this.doc.range[2] = end.offset;
            break;
          }
          default:
            this.errors.push(new errors.YAMLParseError(getErrorPos(token), "UNEXPECTED_TOKEN", `Unsupported token ${token.type}`));
        }
      }
      /**
       * Call at end of input to yield any remaining document.
       *
       * @param forceDoc - If the stream contains no document, still emit a final document including any comments and directives that would be applied to a subsequent document.
       * @param endOffset - Should be set if `forceDoc` is also set, to set the document range end and to indicate errors correctly.
       */
      *end(forceDoc = false, endOffset = -1) {
        if (this.doc) {
          this.decorate(this.doc, true);
          yield this.doc;
          this.doc = null;
        } else if (forceDoc) {
          const opts = Object.assign({ _directives: this.directives }, this.options);
          const doc = new Document.Document(void 0, opts);
          if (this.atDirectives)
            this.onError(endOffset, "MISSING_CHAR", "Missing directives-end indicator line");
          doc.range = [0, endOffset, endOffset];
          this.decorate(doc, false);
          yield doc;
        }
      }
    };
    exports.Composer = Composer;
  }
});

// node_modules/yaml/dist/parse/cst-scalar.js
var require_cst_scalar = __commonJS({
  "node_modules/yaml/dist/parse/cst-scalar.js"(exports) {
    "use strict";
    var resolveBlockScalar = require_resolve_block_scalar();
    var resolveFlowScalar = require_resolve_flow_scalar();
    var errors = require_errors();
    var stringifyString = require_stringifyString();
    function resolveAsScalar(token, strict = true, onError) {
      if (token) {
        const _onError = (pos, code, message) => {
          const offset = typeof pos === "number" ? pos : Array.isArray(pos) ? pos[0] : pos.offset;
          if (onError)
            onError(offset, code, message);
          else
            throw new errors.YAMLParseError([offset, offset + 1], code, message);
        };
        switch (token.type) {
          case "scalar":
          case "single-quoted-scalar":
          case "double-quoted-scalar":
            return resolveFlowScalar.resolveFlowScalar(token, strict, _onError);
          case "block-scalar":
            return resolveBlockScalar.resolveBlockScalar({ options: { strict } }, token, _onError);
        }
      }
      return null;
    }
    function createScalarToken(value, context) {
      const { implicitKey = false, indent, inFlow = false, offset = -1, type = "PLAIN" } = context;
      const source = stringifyString.stringifyString({ type, value }, {
        implicitKey,
        indent: indent > 0 ? " ".repeat(indent) : "",
        inFlow,
        options: { blockQuote: true, lineWidth: -1 }
      });
      const end = context.end ?? [
        { type: "newline", offset: -1, indent, source: "\n" }
      ];
      switch (source[0]) {
        case "|":
        case ">": {
          const he = source.indexOf("\n");
          const head = source.substring(0, he);
          const body = source.substring(he + 1) + "\n";
          const props = [
            { type: "block-scalar-header", offset, indent, source: head }
          ];
          if (!addEndtoBlockProps(props, end))
            props.push({ type: "newline", offset: -1, indent, source: "\n" });
          return { type: "block-scalar", offset, indent, props, source: body };
        }
        case '"':
          return { type: "double-quoted-scalar", offset, indent, source, end };
        case "'":
          return { type: "single-quoted-scalar", offset, indent, source, end };
        default:
          return { type: "scalar", offset, indent, source, end };
      }
    }
    function setScalarValue(token, value, context = {}) {
      let { afterKey = false, implicitKey = false, inFlow = false, type } = context;
      let indent = "indent" in token ? token.indent : null;
      if (afterKey && typeof indent === "number")
        indent += 2;
      if (!type)
        switch (token.type) {
          case "single-quoted-scalar":
            type = "QUOTE_SINGLE";
            break;
          case "double-quoted-scalar":
            type = "QUOTE_DOUBLE";
            break;
          case "block-scalar": {
            const header = token.props[0];
            if (header.type !== "block-scalar-header")
              throw new Error("Invalid block scalar header");
            type = header.source[0] === ">" ? "BLOCK_FOLDED" : "BLOCK_LITERAL";
            break;
          }
          default:
            type = "PLAIN";
        }
      const source = stringifyString.stringifyString({ type, value }, {
        implicitKey: implicitKey || indent === null,
        indent: indent !== null && indent > 0 ? " ".repeat(indent) : "",
        inFlow,
        options: { blockQuote: true, lineWidth: -1 }
      });
      switch (source[0]) {
        case "|":
        case ">":
          setBlockScalarValue(token, source);
          break;
        case '"':
          setFlowScalarValue(token, source, "double-quoted-scalar");
          break;
        case "'":
          setFlowScalarValue(token, source, "single-quoted-scalar");
          break;
        default:
          setFlowScalarValue(token, source, "scalar");
      }
    }
    function setBlockScalarValue(token, source) {
      const he = source.indexOf("\n");
      const head = source.substring(0, he);
      const body = source.substring(he + 1) + "\n";
      if (token.type === "block-scalar") {
        const header = token.props[0];
        if (header.type !== "block-scalar-header")
          throw new Error("Invalid block scalar header");
        header.source = head;
        token.source = body;
      } else {
        const { offset } = token;
        const indent = "indent" in token ? token.indent : -1;
        const props = [
          { type: "block-scalar-header", offset, indent, source: head }
        ];
        if (!addEndtoBlockProps(props, "end" in token ? token.end : void 0))
          props.push({ type: "newline", offset: -1, indent, source: "\n" });
        for (const key of Object.keys(token))
          if (key !== "type" && key !== "offset")
            delete token[key];
        Object.assign(token, { type: "block-scalar", indent, props, source: body });
      }
    }
    function addEndtoBlockProps(props, end) {
      if (end)
        for (const st of end)
          switch (st.type) {
            case "space":
            case "comment":
              props.push(st);
              break;
            case "newline":
              props.push(st);
              return true;
          }
      return false;
    }
    function setFlowScalarValue(token, source, type) {
      switch (token.type) {
        case "scalar":
        case "double-quoted-scalar":
        case "single-quoted-scalar":
          token.type = type;
          token.source = source;
          break;
        case "block-scalar": {
          const end = token.props.slice(1);
          let oa = source.length;
          if (token.props[0].type === "block-scalar-header")
            oa -= token.props[0].source.length;
          for (const tok of end)
            tok.offset += oa;
          delete token.props;
          Object.assign(token, { type, source, end });
          break;
        }
        case "block-map":
        case "block-seq": {
          const offset = token.offset + source.length;
          const nl = { type: "newline", offset, indent: token.indent, source: "\n" };
          delete token.items;
          Object.assign(token, { type, source, end: [nl] });
          break;
        }
        default: {
          const indent = "indent" in token ? token.indent : -1;
          const end = "end" in token && Array.isArray(token.end) ? token.end.filter((st) => st.type === "space" || st.type === "comment" || st.type === "newline") : [];
          for (const key of Object.keys(token))
            if (key !== "type" && key !== "offset")
              delete token[key];
          Object.assign(token, { type, indent, source, end });
        }
      }
    }
    exports.createScalarToken = createScalarToken;
    exports.resolveAsScalar = resolveAsScalar;
    exports.setScalarValue = setScalarValue;
  }
});

// node_modules/yaml/dist/parse/cst-stringify.js
var require_cst_stringify = __commonJS({
  "node_modules/yaml/dist/parse/cst-stringify.js"(exports) {
    "use strict";
    var stringify = (cst) => "type" in cst ? stringifyToken(cst) : stringifyItem(cst);
    function stringifyToken(token) {
      switch (token.type) {
        case "block-scalar": {
          let res = "";
          for (const tok of token.props)
            res += stringifyToken(tok);
          return res + token.source;
        }
        case "block-map":
        case "block-seq": {
          let res = "";
          for (const item of token.items)
            res += stringifyItem(item);
          return res;
        }
        case "flow-collection": {
          let res = token.start.source;
          for (const item of token.items)
            res += stringifyItem(item);
          for (const st of token.end)
            res += st.source;
          return res;
        }
        case "document": {
          let res = stringifyItem(token);
          if (token.end)
            for (const st of token.end)
              res += st.source;
          return res;
        }
        default: {
          let res = token.source;
          if ("end" in token && token.end)
            for (const st of token.end)
              res += st.source;
          return res;
        }
      }
    }
    function stringifyItem({ start, key, sep, value }) {
      let res = "";
      for (const st of start)
        res += st.source;
      if (key)
        res += stringifyToken(key);
      if (sep)
        for (const st of sep)
          res += st.source;
      if (value)
        res += stringifyToken(value);
      return res;
    }
    exports.stringify = stringify;
  }
});

// node_modules/yaml/dist/parse/cst-visit.js
var require_cst_visit = __commonJS({
  "node_modules/yaml/dist/parse/cst-visit.js"(exports) {
    "use strict";
    var BREAK = /* @__PURE__ */ Symbol("break visit");
    var SKIP = /* @__PURE__ */ Symbol("skip children");
    var REMOVE = /* @__PURE__ */ Symbol("remove item");
    function visit(cst, visitor) {
      if ("type" in cst && cst.type === "document")
        cst = { start: cst.start, value: cst.value };
      _visit(Object.freeze([]), cst, visitor);
    }
    visit.BREAK = BREAK;
    visit.SKIP = SKIP;
    visit.REMOVE = REMOVE;
    visit.itemAtPath = (cst, path16) => {
      let item = cst;
      for (const [field, index] of path16) {
        const tok = item?.[field];
        if (tok && "items" in tok) {
          item = tok.items[index];
        } else
          return void 0;
      }
      return item;
    };
    visit.parentCollection = (cst, path16) => {
      const parent = visit.itemAtPath(cst, path16.slice(0, -1));
      const field = path16[path16.length - 1][0];
      const coll = parent?.[field];
      if (coll && "items" in coll)
        return coll;
      throw new Error("Parent collection not found");
    };
    function _visit(path16, item, visitor) {
      let ctrl = visitor(item, path16);
      if (typeof ctrl === "symbol")
        return ctrl;
      for (const field of ["key", "value"]) {
        const token = item[field];
        if (token && "items" in token) {
          for (let i = 0; i < token.items.length; ++i) {
            const ci = _visit(Object.freeze(path16.concat([[field, i]])), token.items[i], visitor);
            if (typeof ci === "number")
              i = ci - 1;
            else if (ci === BREAK)
              return BREAK;
            else if (ci === REMOVE) {
              token.items.splice(i, 1);
              i -= 1;
            }
          }
          if (typeof ctrl === "function" && field === "key")
            ctrl = ctrl(item, path16);
        }
      }
      return typeof ctrl === "function" ? ctrl(item, path16) : ctrl;
    }
    exports.visit = visit;
  }
});

// node_modules/yaml/dist/parse/cst.js
var require_cst = __commonJS({
  "node_modules/yaml/dist/parse/cst.js"(exports) {
    "use strict";
    var cstScalar = require_cst_scalar();
    var cstStringify = require_cst_stringify();
    var cstVisit = require_cst_visit();
    var BOM = "\uFEFF";
    var DOCUMENT = "";
    var FLOW_END = "";
    var SCALAR = "";
    var isCollection = (token) => !!token && "items" in token;
    var isScalar = (token) => !!token && (token.type === "scalar" || token.type === "single-quoted-scalar" || token.type === "double-quoted-scalar" || token.type === "block-scalar");
    function prettyToken(token) {
      switch (token) {
        case BOM:
          return "<BOM>";
        case DOCUMENT:
          return "<DOC>";
        case FLOW_END:
          return "<FLOW_END>";
        case SCALAR:
          return "<SCALAR>";
        default:
          return JSON.stringify(token);
      }
    }
    function tokenType(source) {
      switch (source) {
        case BOM:
          return "byte-order-mark";
        case DOCUMENT:
          return "doc-mode";
        case FLOW_END:
          return "flow-error-end";
        case SCALAR:
          return "scalar";
        case "---":
          return "doc-start";
        case "...":
          return "doc-end";
        case "":
        case "\n":
        case "\r\n":
          return "newline";
        case "-":
          return "seq-item-ind";
        case "?":
          return "explicit-key-ind";
        case ":":
          return "map-value-ind";
        case "{":
          return "flow-map-start";
        case "}":
          return "flow-map-end";
        case "[":
          return "flow-seq-start";
        case "]":
          return "flow-seq-end";
        case ",":
          return "comma";
      }
      switch (source[0]) {
        case " ":
        case "	":
          return "space";
        case "#":
          return "comment";
        case "%":
          return "directive-line";
        case "*":
          return "alias";
        case "&":
          return "anchor";
        case "!":
          return "tag";
        case "'":
          return "single-quoted-scalar";
        case '"':
          return "double-quoted-scalar";
        case "|":
        case ">":
          return "block-scalar-header";
      }
      return null;
    }
    exports.createScalarToken = cstScalar.createScalarToken;
    exports.resolveAsScalar = cstScalar.resolveAsScalar;
    exports.setScalarValue = cstScalar.setScalarValue;
    exports.stringify = cstStringify.stringify;
    exports.visit = cstVisit.visit;
    exports.BOM = BOM;
    exports.DOCUMENT = DOCUMENT;
    exports.FLOW_END = FLOW_END;
    exports.SCALAR = SCALAR;
    exports.isCollection = isCollection;
    exports.isScalar = isScalar;
    exports.prettyToken = prettyToken;
    exports.tokenType = tokenType;
  }
});

// node_modules/yaml/dist/parse/lexer.js
var require_lexer = __commonJS({
  "node_modules/yaml/dist/parse/lexer.js"(exports) {
    "use strict";
    var cst = require_cst();
    function isEmpty(ch) {
      switch (ch) {
        case void 0:
        case " ":
        case "\n":
        case "\r":
        case "	":
          return true;
        default:
          return false;
      }
    }
    var hexDigits = new Set("0123456789ABCDEFabcdef");
    var tagChars = new Set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-#;/?:@&=+$_.!~*'()");
    var flowIndicatorChars = new Set(",[]{}");
    var invalidAnchorChars = new Set(" ,[]{}\n\r	");
    var isNotAnchorChar = (ch) => !ch || invalidAnchorChars.has(ch);
    var Lexer = class {
      constructor() {
        this.atEnd = false;
        this.blockScalarIndent = -1;
        this.blockScalarKeep = false;
        this.buffer = "";
        this.flowKey = false;
        this.flowLevel = 0;
        this.indentNext = 0;
        this.indentValue = 0;
        this.lineEndPos = null;
        this.next = null;
        this.pos = 0;
      }
      /**
       * Generate YAML tokens from the `source` string. If `incomplete`,
       * a part of the last line may be left as a buffer for the next call.
       *
       * @returns A generator of lexical tokens
       */
      *lex(source, incomplete = false) {
        if (source) {
          if (typeof source !== "string")
            throw TypeError("source is not a string");
          this.buffer = this.buffer ? this.buffer + source : source;
          this.lineEndPos = null;
        }
        this.atEnd = !incomplete;
        let next = this.next ?? "stream";
        while (next && (incomplete || this.hasChars(1)))
          next = yield* this.parseNext(next);
      }
      atLineEnd() {
        let i = this.pos;
        let ch = this.buffer[i];
        while (ch === " " || ch === "	")
          ch = this.buffer[++i];
        if (!ch || ch === "#" || ch === "\n")
          return true;
        if (ch === "\r")
          return this.buffer[i + 1] === "\n";
        return false;
      }
      charAt(n) {
        return this.buffer[this.pos + n];
      }
      continueScalar(offset) {
        let ch = this.buffer[offset];
        if (this.indentNext > 0) {
          let indent = 0;
          while (ch === " ")
            ch = this.buffer[++indent + offset];
          if (ch === "\r") {
            const next = this.buffer[indent + offset + 1];
            if (next === "\n" || !next && !this.atEnd)
              return offset + indent + 1;
          }
          return ch === "\n" || indent >= this.indentNext || !ch && !this.atEnd ? offset + indent : -1;
        }
        if (ch === "-" || ch === ".") {
          const dt = this.buffer.substr(offset, 3);
          if ((dt === "---" || dt === "...") && isEmpty(this.buffer[offset + 3]))
            return -1;
        }
        return offset;
      }
      getLine() {
        let end = this.lineEndPos;
        if (typeof end !== "number" || end !== -1 && end < this.pos) {
          end = this.buffer.indexOf("\n", this.pos);
          this.lineEndPos = end;
        }
        if (end === -1)
          return this.atEnd ? this.buffer.substring(this.pos) : null;
        if (this.buffer[end - 1] === "\r")
          end -= 1;
        return this.buffer.substring(this.pos, end);
      }
      hasChars(n) {
        return this.pos + n <= this.buffer.length;
      }
      setNext(state) {
        this.buffer = this.buffer.substring(this.pos);
        this.pos = 0;
        this.lineEndPos = null;
        this.next = state;
        return null;
      }
      peek(n) {
        return this.buffer.substr(this.pos, n);
      }
      *parseNext(next) {
        switch (next) {
          case "stream":
            return yield* this.parseStream();
          case "line-start":
            return yield* this.parseLineStart();
          case "block-start":
            return yield* this.parseBlockStart();
          case "doc":
            return yield* this.parseDocument();
          case "flow":
            return yield* this.parseFlowCollection();
          case "quoted-scalar":
            return yield* this.parseQuotedScalar();
          case "block-scalar":
            return yield* this.parseBlockScalar();
          case "plain-scalar":
            return yield* this.parsePlainScalar();
        }
      }
      *parseStream() {
        let line = this.getLine();
        if (line === null)
          return this.setNext("stream");
        if (line[0] === cst.BOM) {
          yield* this.pushCount(1);
          line = line.substring(1);
        }
        if (line[0] === "%") {
          let dirEnd = line.length;
          let cs = line.indexOf("#");
          while (cs !== -1) {
            const ch = line[cs - 1];
            if (ch === " " || ch === "	") {
              dirEnd = cs - 1;
              break;
            } else {
              cs = line.indexOf("#", cs + 1);
            }
          }
          while (true) {
            const ch = line[dirEnd - 1];
            if (ch === " " || ch === "	")
              dirEnd -= 1;
            else
              break;
          }
          const n = (yield* this.pushCount(dirEnd)) + (yield* this.pushSpaces(true));
          yield* this.pushCount(line.length - n);
          this.pushNewline();
          return "stream";
        }
        if (this.atLineEnd()) {
          const sp = yield* this.pushSpaces(true);
          yield* this.pushCount(line.length - sp);
          yield* this.pushNewline();
          return "stream";
        }
        yield cst.DOCUMENT;
        return yield* this.parseLineStart();
      }
      *parseLineStart() {
        const ch = this.charAt(0);
        if (!ch && !this.atEnd)
          return this.setNext("line-start");
        if (ch === "-" || ch === ".") {
          if (!this.atEnd && !this.hasChars(4))
            return this.setNext("line-start");
          const s = this.peek(3);
          if ((s === "---" || s === "...") && isEmpty(this.charAt(3))) {
            yield* this.pushCount(3);
            this.indentValue = 0;
            this.indentNext = 0;
            return s === "---" ? "doc" : "stream";
          }
        }
        this.indentValue = yield* this.pushSpaces(false);
        if (this.indentNext > this.indentValue && !isEmpty(this.charAt(1)))
          this.indentNext = this.indentValue;
        return yield* this.parseBlockStart();
      }
      *parseBlockStart() {
        const [ch0, ch1] = this.peek(2);
        if (!ch1 && !this.atEnd)
          return this.setNext("block-start");
        if ((ch0 === "-" || ch0 === "?" || ch0 === ":") && isEmpty(ch1)) {
          const n = (yield* this.pushCount(1)) + (yield* this.pushSpaces(true));
          this.indentNext = this.indentValue + 1;
          this.indentValue += n;
          return "block-start";
        }
        return "doc";
      }
      *parseDocument() {
        yield* this.pushSpaces(true);
        const line = this.getLine();
        if (line === null)
          return this.setNext("doc");
        let n = yield* this.pushIndicators();
        switch (line[n]) {
          case "#":
            yield* this.pushCount(line.length - n);
          // fallthrough
          case void 0:
            yield* this.pushNewline();
            return yield* this.parseLineStart();
          case "{":
          case "[":
            yield* this.pushCount(1);
            this.flowKey = false;
            this.flowLevel = 1;
            return "flow";
          case "}":
          case "]":
            yield* this.pushCount(1);
            return "doc";
          case "*":
            yield* this.pushUntil(isNotAnchorChar);
            return "doc";
          case '"':
          case "'":
            return yield* this.parseQuotedScalar();
          case "|":
          case ">":
            n += yield* this.parseBlockScalarHeader();
            n += yield* this.pushSpaces(true);
            yield* this.pushCount(line.length - n);
            yield* this.pushNewline();
            return yield* this.parseBlockScalar();
          default:
            return yield* this.parsePlainScalar();
        }
      }
      *parseFlowCollection() {
        let nl, sp;
        let indent = -1;
        do {
          nl = yield* this.pushNewline();
          if (nl > 0) {
            sp = yield* this.pushSpaces(false);
            this.indentValue = indent = sp;
          } else {
            sp = 0;
          }
          sp += yield* this.pushSpaces(true);
        } while (nl + sp > 0);
        const line = this.getLine();
        if (line === null)
          return this.setNext("flow");
        if (indent !== -1 && indent < this.indentNext && line[0] !== "#" || indent === 0 && (line.startsWith("---") || line.startsWith("...")) && isEmpty(line[3])) {
          const atFlowEndMarker = indent === this.indentNext - 1 && this.flowLevel === 1 && (line[0] === "]" || line[0] === "}");
          if (!atFlowEndMarker) {
            this.flowLevel = 0;
            yield cst.FLOW_END;
            return yield* this.parseLineStart();
          }
        }
        let n = 0;
        while (line[n] === ",") {
          n += yield* this.pushCount(1);
          n += yield* this.pushSpaces(true);
          this.flowKey = false;
        }
        n += yield* this.pushIndicators();
        switch (line[n]) {
          case void 0:
            return "flow";
          case "#":
            yield* this.pushCount(line.length - n);
            return "flow";
          case "{":
          case "[":
            yield* this.pushCount(1);
            this.flowKey = false;
            this.flowLevel += 1;
            return "flow";
          case "}":
          case "]":
            yield* this.pushCount(1);
            this.flowKey = true;
            this.flowLevel -= 1;
            return this.flowLevel ? "flow" : "doc";
          case "*":
            yield* this.pushUntil(isNotAnchorChar);
            return "flow";
          case '"':
          case "'":
            this.flowKey = true;
            return yield* this.parseQuotedScalar();
          case ":": {
            const next = this.charAt(1);
            if (this.flowKey || isEmpty(next) || next === ",") {
              this.flowKey = false;
              yield* this.pushCount(1);
              yield* this.pushSpaces(true);
              return "flow";
            }
          }
          // fallthrough
          default:
            this.flowKey = false;
            return yield* this.parsePlainScalar();
        }
      }
      *parseQuotedScalar() {
        const quote = this.charAt(0);
        let end = this.buffer.indexOf(quote, this.pos + 1);
        if (quote === "'") {
          while (end !== -1 && this.buffer[end + 1] === "'")
            end = this.buffer.indexOf("'", end + 2);
        } else {
          while (end !== -1) {
            let n = 0;
            while (this.buffer[end - 1 - n] === "\\")
              n += 1;
            if (n % 2 === 0)
              break;
            end = this.buffer.indexOf('"', end + 1);
          }
        }
        const qb = this.buffer.substring(0, end);
        let nl = qb.indexOf("\n", this.pos);
        if (nl !== -1) {
          while (nl !== -1) {
            const cs = this.continueScalar(nl + 1);
            if (cs === -1)
              break;
            nl = qb.indexOf("\n", cs);
          }
          if (nl !== -1) {
            end = nl - (qb[nl - 1] === "\r" ? 2 : 1);
          }
        }
        if (end === -1) {
          if (!this.atEnd)
            return this.setNext("quoted-scalar");
          end = this.buffer.length;
        }
        yield* this.pushToIndex(end + 1, false);
        return this.flowLevel ? "flow" : "doc";
      }
      *parseBlockScalarHeader() {
        this.blockScalarIndent = -1;
        this.blockScalarKeep = false;
        let i = this.pos;
        while (true) {
          const ch = this.buffer[++i];
          if (ch === "+")
            this.blockScalarKeep = true;
          else if (ch > "0" && ch <= "9")
            this.blockScalarIndent = Number(ch) - 1;
          else if (ch !== "-")
            break;
        }
        return yield* this.pushUntil((ch) => isEmpty(ch) || ch === "#");
      }
      *parseBlockScalar() {
        let nl = this.pos - 1;
        let indent = 0;
        let ch;
        loop: for (let i2 = this.pos; ch = this.buffer[i2]; ++i2) {
          switch (ch) {
            case " ":
              indent += 1;
              break;
            case "\n":
              nl = i2;
              indent = 0;
              break;
            case "\r": {
              const next = this.buffer[i2 + 1];
              if (!next && !this.atEnd)
                return this.setNext("block-scalar");
              if (next === "\n")
                break;
            }
            // fallthrough
            default:
              break loop;
          }
        }
        if (!ch && !this.atEnd)
          return this.setNext("block-scalar");
        if (indent >= this.indentNext) {
          if (this.blockScalarIndent === -1)
            this.indentNext = indent;
          else {
            this.indentNext = this.blockScalarIndent + (this.indentNext === 0 ? 1 : this.indentNext);
          }
          do {
            const cs = this.continueScalar(nl + 1);
            if (cs === -1)
              break;
            nl = this.buffer.indexOf("\n", cs);
          } while (nl !== -1);
          if (nl === -1) {
            if (!this.atEnd)
              return this.setNext("block-scalar");
            nl = this.buffer.length;
          }
        }
        let i = nl + 1;
        ch = this.buffer[i];
        while (ch === " ")
          ch = this.buffer[++i];
        if (ch === "	") {
          while (ch === "	" || ch === " " || ch === "\r" || ch === "\n")
            ch = this.buffer[++i];
          nl = i - 1;
        } else if (!this.blockScalarKeep) {
          do {
            let i2 = nl - 1;
            let ch2 = this.buffer[i2];
            if (ch2 === "\r")
              ch2 = this.buffer[--i2];
            const lastChar = i2;
            while (ch2 === " ")
              ch2 = this.buffer[--i2];
            if (ch2 === "\n" && i2 >= this.pos && i2 + 1 + indent > lastChar)
              nl = i2;
            else
              break;
          } while (true);
        }
        yield cst.SCALAR;
        yield* this.pushToIndex(nl + 1, true);
        return yield* this.parseLineStart();
      }
      *parsePlainScalar() {
        const inFlow = this.flowLevel > 0;
        let end = this.pos - 1;
        let i = this.pos - 1;
        let ch;
        while (ch = this.buffer[++i]) {
          if (ch === ":") {
            const next = this.buffer[i + 1];
            if (isEmpty(next) || inFlow && flowIndicatorChars.has(next))
              break;
            end = i;
          } else if (isEmpty(ch)) {
            let next = this.buffer[i + 1];
            if (ch === "\r") {
              if (next === "\n") {
                i += 1;
                ch = "\n";
                next = this.buffer[i + 1];
              } else
                end = i;
            }
            if (next === "#" || inFlow && flowIndicatorChars.has(next))
              break;
            if (ch === "\n") {
              const cs = this.continueScalar(i + 1);
              if (cs === -1)
                break;
              i = Math.max(i, cs - 2);
            }
          } else {
            if (inFlow && flowIndicatorChars.has(ch))
              break;
            end = i;
          }
        }
        if (!ch && !this.atEnd)
          return this.setNext("plain-scalar");
        yield cst.SCALAR;
        yield* this.pushToIndex(end + 1, true);
        return inFlow ? "flow" : "doc";
      }
      *pushCount(n) {
        if (n > 0) {
          yield this.buffer.substr(this.pos, n);
          this.pos += n;
          return n;
        }
        return 0;
      }
      *pushToIndex(i, allowEmpty) {
        const s = this.buffer.slice(this.pos, i);
        if (s) {
          yield s;
          this.pos += s.length;
          return s.length;
        } else if (allowEmpty)
          yield "";
        return 0;
      }
      *pushIndicators() {
        let n = 0;
        loop: while (true) {
          switch (this.charAt(0)) {
            case "!":
              n += yield* this.pushTag();
              n += yield* this.pushSpaces(true);
              continue loop;
            case "&":
              n += yield* this.pushUntil(isNotAnchorChar);
              n += yield* this.pushSpaces(true);
              continue loop;
            case "-":
            // this is an error
            case "?":
            // this is an error outside flow collections
            case ":": {
              const inFlow = this.flowLevel > 0;
              const ch1 = this.charAt(1);
              if (isEmpty(ch1) || inFlow && flowIndicatorChars.has(ch1)) {
                if (!inFlow)
                  this.indentNext = this.indentValue + 1;
                else if (this.flowKey)
                  this.flowKey = false;
                n += yield* this.pushCount(1);
                n += yield* this.pushSpaces(true);
                continue loop;
              }
            }
          }
          break loop;
        }
        return n;
      }
      *pushTag() {
        if (this.charAt(1) === "<") {
          let i = this.pos + 2;
          let ch = this.buffer[i];
          while (!isEmpty(ch) && ch !== ">")
            ch = this.buffer[++i];
          return yield* this.pushToIndex(ch === ">" ? i + 1 : i, false);
        } else {
          let i = this.pos + 1;
          let ch = this.buffer[i];
          while (ch) {
            if (tagChars.has(ch))
              ch = this.buffer[++i];
            else if (ch === "%" && hexDigits.has(this.buffer[i + 1]) && hexDigits.has(this.buffer[i + 2])) {
              ch = this.buffer[i += 3];
            } else
              break;
          }
          return yield* this.pushToIndex(i, false);
        }
      }
      *pushNewline() {
        const ch = this.buffer[this.pos];
        if (ch === "\n")
          return yield* this.pushCount(1);
        else if (ch === "\r" && this.charAt(1) === "\n")
          return yield* this.pushCount(2);
        else
          return 0;
      }
      *pushSpaces(allowTabs) {
        let i = this.pos - 1;
        let ch;
        do {
          ch = this.buffer[++i];
        } while (ch === " " || allowTabs && ch === "	");
        const n = i - this.pos;
        if (n > 0) {
          yield this.buffer.substr(this.pos, n);
          this.pos = i;
        }
        return n;
      }
      *pushUntil(test) {
        let i = this.pos;
        let ch = this.buffer[i];
        while (!test(ch))
          ch = this.buffer[++i];
        return yield* this.pushToIndex(i, false);
      }
    };
    exports.Lexer = Lexer;
  }
});

// node_modules/yaml/dist/parse/line-counter.js
var require_line_counter = __commonJS({
  "node_modules/yaml/dist/parse/line-counter.js"(exports) {
    "use strict";
    var LineCounter = class {
      constructor() {
        this.lineStarts = [];
        this.addNewLine = (offset) => this.lineStarts.push(offset);
        this.linePos = (offset) => {
          let low = 0;
          let high = this.lineStarts.length;
          while (low < high) {
            const mid = low + high >> 1;
            if (this.lineStarts[mid] < offset)
              low = mid + 1;
            else
              high = mid;
          }
          if (this.lineStarts[low] === offset)
            return { line: low + 1, col: 1 };
          if (low === 0)
            return { line: 0, col: offset };
          const start = this.lineStarts[low - 1];
          return { line: low, col: offset - start + 1 };
        };
      }
    };
    exports.LineCounter = LineCounter;
  }
});

// node_modules/yaml/dist/parse/parser.js
var require_parser = __commonJS({
  "node_modules/yaml/dist/parse/parser.js"(exports) {
    "use strict";
    var node_process = __require("process");
    var cst = require_cst();
    var lexer = require_lexer();
    function includesToken(list3, type) {
      for (let i = 0; i < list3.length; ++i)
        if (list3[i].type === type)
          return true;
      return false;
    }
    function findNonEmptyIndex(list3) {
      for (let i = 0; i < list3.length; ++i) {
        switch (list3[i].type) {
          case "space":
          case "comment":
          case "newline":
            break;
          default:
            return i;
        }
      }
      return -1;
    }
    function isFlowToken(token) {
      switch (token?.type) {
        case "alias":
        case "scalar":
        case "single-quoted-scalar":
        case "double-quoted-scalar":
        case "flow-collection":
          return true;
        default:
          return false;
      }
    }
    function getPrevProps(parent) {
      switch (parent.type) {
        case "document":
          return parent.start;
        case "block-map": {
          const it = parent.items[parent.items.length - 1];
          return it.sep ?? it.start;
        }
        case "block-seq":
          return parent.items[parent.items.length - 1].start;
        /* istanbul ignore next should not happen */
        default:
          return [];
      }
    }
    function getFirstKeyStartProps(prev) {
      if (prev.length === 0)
        return [];
      let i = prev.length;
      loop: while (--i >= 0) {
        switch (prev[i].type) {
          case "doc-start":
          case "explicit-key-ind":
          case "map-value-ind":
          case "seq-item-ind":
          case "newline":
            break loop;
        }
      }
      while (prev[++i]?.type === "space") {
      }
      return prev.splice(i, prev.length);
    }
    function arrayPushArray(target, source) {
      if (source.length < 1e5)
        Array.prototype.push.apply(target, source);
      else
        for (let i = 0; i < source.length; ++i)
          target.push(source[i]);
    }
    function fixFlowSeqItems(fc) {
      if (fc.start.type === "flow-seq-start") {
        for (const it of fc.items) {
          if (it.sep && !it.value && !includesToken(it.start, "explicit-key-ind") && !includesToken(it.sep, "map-value-ind")) {
            if (it.key)
              it.value = it.key;
            delete it.key;
            if (isFlowToken(it.value)) {
              if (it.value.end)
                arrayPushArray(it.value.end, it.sep);
              else
                it.value.end = it.sep;
            } else
              arrayPushArray(it.start, it.sep);
            delete it.sep;
          }
        }
      }
    }
    var Parser = class {
      /**
       * @param onNewLine - If defined, called separately with the start position of
       *   each new line (in `parse()`, including the start of input).
       */
      constructor(onNewLine) {
        this.atNewLine = true;
        this.atScalar = false;
        this.indent = 0;
        this.offset = 0;
        this.onKeyLine = false;
        this.stack = [];
        this.source = "";
        this.type = "";
        this.lexer = new lexer.Lexer();
        this.onNewLine = onNewLine;
      }
      /**
       * Parse `source` as a YAML stream.
       * If `incomplete`, a part of the last line may be left as a buffer for the next call.
       *
       * Errors are not thrown, but yielded as `{ type: 'error', message }` tokens.
       *
       * @returns A generator of tokens representing each directive, document, and other structure.
       */
      *parse(source, incomplete = false) {
        if (this.onNewLine && this.offset === 0)
          this.onNewLine(0);
        for (const lexeme of this.lexer.lex(source, incomplete))
          yield* this.next(lexeme);
        if (!incomplete)
          yield* this.end();
      }
      /**
       * Advance the parser by the `source` of one lexical token.
       */
      *next(source) {
        this.source = source;
        if (node_process.env.LOG_TOKENS)
          console.log("|", cst.prettyToken(source));
        if (this.atScalar) {
          this.atScalar = false;
          yield* this.step();
          this.offset += source.length;
          return;
        }
        const type = cst.tokenType(source);
        if (!type) {
          const message = `Not a YAML token: ${source}`;
          yield* this.pop({ type: "error", offset: this.offset, message, source });
          this.offset += source.length;
        } else if (type === "scalar") {
          this.atNewLine = false;
          this.atScalar = true;
          this.type = "scalar";
        } else {
          this.type = type;
          yield* this.step();
          switch (type) {
            case "newline":
              this.atNewLine = true;
              this.indent = 0;
              if (this.onNewLine)
                this.onNewLine(this.offset + source.length);
              break;
            case "space":
              if (this.atNewLine && source[0] === " ")
                this.indent += source.length;
              break;
            case "explicit-key-ind":
            case "map-value-ind":
            case "seq-item-ind":
              if (this.atNewLine)
                this.indent += source.length;
              break;
            case "doc-mode":
            case "flow-error-end":
              return;
            default:
              this.atNewLine = false;
          }
          this.offset += source.length;
        }
      }
      /** Call at end of input to push out any remaining constructions */
      *end() {
        while (this.stack.length > 0)
          yield* this.pop();
      }
      get sourceToken() {
        const st = {
          type: this.type,
          offset: this.offset,
          indent: this.indent,
          source: this.source
        };
        return st;
      }
      *step() {
        const top = this.peek(1);
        if (this.type === "doc-end" && top?.type !== "doc-end") {
          while (this.stack.length > 0)
            yield* this.pop();
          this.stack.push({
            type: "doc-end",
            offset: this.offset,
            source: this.source
          });
          return;
        }
        if (!top)
          return yield* this.stream();
        switch (top.type) {
          case "document":
            return yield* this.document(top);
          case "alias":
          case "scalar":
          case "single-quoted-scalar":
          case "double-quoted-scalar":
            return yield* this.scalar(top);
          case "block-scalar":
            return yield* this.blockScalar(top);
          case "block-map":
            return yield* this.blockMap(top);
          case "block-seq":
            return yield* this.blockSequence(top);
          case "flow-collection":
            return yield* this.flowCollection(top);
          case "doc-end":
            return yield* this.documentEnd(top);
        }
        yield* this.pop();
      }
      peek(n) {
        return this.stack[this.stack.length - n];
      }
      *pop(error) {
        const token = error ?? this.stack.pop();
        if (!token) {
          const message = "Tried to pop an empty stack";
          yield { type: "error", offset: this.offset, source: "", message };
        } else if (this.stack.length === 0) {
          yield token;
        } else {
          const top = this.peek(1);
          if (token.type === "block-scalar") {
            token.indent = "indent" in top ? top.indent : 0;
          } else if (token.type === "flow-collection" && top.type === "document") {
            token.indent = 0;
          }
          if (token.type === "flow-collection")
            fixFlowSeqItems(token);
          switch (top.type) {
            case "document":
              top.value = token;
              break;
            case "block-scalar":
              top.props.push(token);
              break;
            case "block-map": {
              const it = top.items[top.items.length - 1];
              if (it.value) {
                top.items.push({ start: [], key: token, sep: [] });
                this.onKeyLine = true;
                return;
              } else if (it.sep) {
                it.value = token;
              } else {
                Object.assign(it, { key: token, sep: [] });
                this.onKeyLine = !it.explicitKey;
                return;
              }
              break;
            }
            case "block-seq": {
              const it = top.items[top.items.length - 1];
              if (it.value)
                top.items.push({ start: [], value: token });
              else
                it.value = token;
              break;
            }
            case "flow-collection": {
              const it = top.items[top.items.length - 1];
              if (!it || it.value)
                top.items.push({ start: [], key: token, sep: [] });
              else if (it.sep)
                it.value = token;
              else
                Object.assign(it, { key: token, sep: [] });
              return;
            }
            /* istanbul ignore next should not happen */
            default:
              yield* this.pop();
              yield* this.pop(token);
          }
          if ((top.type === "document" || top.type === "block-map" || top.type === "block-seq") && (token.type === "block-map" || token.type === "block-seq")) {
            const last = token.items[token.items.length - 1];
            if (last && !last.sep && !last.value && last.start.length > 0 && findNonEmptyIndex(last.start) === -1 && (token.indent === 0 || last.start.every((st) => st.type !== "comment" || st.indent < token.indent))) {
              if (top.type === "document")
                top.end = last.start;
              else
                top.items.push({ start: last.start });
              token.items.splice(-1, 1);
            }
          }
        }
      }
      *stream() {
        switch (this.type) {
          case "directive-line":
            yield { type: "directive", offset: this.offset, source: this.source };
            return;
          case "byte-order-mark":
          case "space":
          case "comment":
          case "newline":
            yield this.sourceToken;
            return;
          case "doc-mode":
          case "doc-start": {
            const doc = {
              type: "document",
              offset: this.offset,
              start: []
            };
            if (this.type === "doc-start")
              doc.start.push(this.sourceToken);
            this.stack.push(doc);
            return;
          }
        }
        yield {
          type: "error",
          offset: this.offset,
          message: `Unexpected ${this.type} token in YAML stream`,
          source: this.source
        };
      }
      *document(doc) {
        if (doc.value)
          return yield* this.lineEnd(doc);
        switch (this.type) {
          case "doc-start": {
            if (findNonEmptyIndex(doc.start) !== -1) {
              yield* this.pop();
              yield* this.step();
            } else
              doc.start.push(this.sourceToken);
            return;
          }
          case "anchor":
          case "tag":
          case "space":
          case "comment":
          case "newline":
            doc.start.push(this.sourceToken);
            return;
        }
        const bv = this.startBlockValue(doc);
        if (bv)
          this.stack.push(bv);
        else {
          yield {
            type: "error",
            offset: this.offset,
            message: `Unexpected ${this.type} token in YAML document`,
            source: this.source
          };
        }
      }
      *scalar(scalar) {
        if (this.type === "map-value-ind") {
          const prev = getPrevProps(this.peek(2));
          const start = getFirstKeyStartProps(prev);
          let sep;
          if (scalar.end) {
            sep = scalar.end;
            sep.push(this.sourceToken);
            delete scalar.end;
          } else
            sep = [this.sourceToken];
          const map = {
            type: "block-map",
            offset: scalar.offset,
            indent: scalar.indent,
            items: [{ start, key: scalar, sep }]
          };
          this.onKeyLine = true;
          this.stack[this.stack.length - 1] = map;
        } else
          yield* this.lineEnd(scalar);
      }
      *blockScalar(scalar) {
        switch (this.type) {
          case "space":
          case "comment":
          case "newline":
            scalar.props.push(this.sourceToken);
            return;
          case "scalar":
            scalar.source = this.source;
            this.atNewLine = true;
            this.indent = 0;
            if (this.onNewLine) {
              let nl = this.source.indexOf("\n") + 1;
              while (nl !== 0) {
                this.onNewLine(this.offset + nl);
                nl = this.source.indexOf("\n", nl) + 1;
              }
            }
            yield* this.pop();
            break;
          /* istanbul ignore next should not happen */
          default:
            yield* this.pop();
            yield* this.step();
        }
      }
      *blockMap(map) {
        const it = map.items[map.items.length - 1];
        switch (this.type) {
          case "newline":
            this.onKeyLine = false;
            if (it.value) {
              const end = "end" in it.value ? it.value.end : void 0;
              const last = Array.isArray(end) ? end[end.length - 1] : void 0;
              if (last?.type === "comment")
                end?.push(this.sourceToken);
              else
                map.items.push({ start: [this.sourceToken] });
            } else if (it.sep) {
              it.sep.push(this.sourceToken);
            } else {
              it.start.push(this.sourceToken);
            }
            return;
          case "space":
          case "comment":
            if (it.value) {
              map.items.push({ start: [this.sourceToken] });
            } else if (it.sep) {
              it.sep.push(this.sourceToken);
            } else {
              if (this.atIndentedComment(it.start, map.indent)) {
                const prev = map.items[map.items.length - 2];
                const end = prev?.value?.end;
                if (Array.isArray(end)) {
                  arrayPushArray(end, it.start);
                  end.push(this.sourceToken);
                  map.items.pop();
                  return;
                }
              }
              it.start.push(this.sourceToken);
            }
            return;
        }
        if (this.indent >= map.indent) {
          const atMapIndent = !this.onKeyLine && this.indent === map.indent;
          const atNextItem = atMapIndent && (it.sep || it.explicitKey) && this.type !== "seq-item-ind";
          let start = [];
          if (atNextItem && it.sep && !it.value) {
            const nl = [];
            for (let i = 0; i < it.sep.length; ++i) {
              const st = it.sep[i];
              switch (st.type) {
                case "newline":
                  nl.push(i);
                  break;
                case "space":
                  break;
                case "comment":
                  if (st.indent > map.indent)
                    nl.length = 0;
                  break;
                default:
                  nl.length = 0;
              }
            }
            if (nl.length >= 2)
              start = it.sep.splice(nl[1]);
          }
          switch (this.type) {
            case "anchor":
            case "tag":
              if (atNextItem || it.value) {
                start.push(this.sourceToken);
                map.items.push({ start });
                this.onKeyLine = true;
              } else if (it.sep) {
                it.sep.push(this.sourceToken);
              } else {
                it.start.push(this.sourceToken);
              }
              return;
            case "explicit-key-ind":
              if (!it.sep && !it.explicitKey) {
                it.start.push(this.sourceToken);
                it.explicitKey = true;
              } else if (atNextItem || it.value) {
                start.push(this.sourceToken);
                map.items.push({ start, explicitKey: true });
              } else {
                this.stack.push({
                  type: "block-map",
                  offset: this.offset,
                  indent: this.indent,
                  items: [{ start: [this.sourceToken], explicitKey: true }]
                });
              }
              this.onKeyLine = true;
              return;
            case "map-value-ind":
              if (it.explicitKey) {
                if (!it.sep) {
                  if (includesToken(it.start, "newline")) {
                    Object.assign(it, { key: null, sep: [this.sourceToken] });
                  } else {
                    const start2 = getFirstKeyStartProps(it.start);
                    this.stack.push({
                      type: "block-map",
                      offset: this.offset,
                      indent: this.indent,
                      items: [{ start: start2, key: null, sep: [this.sourceToken] }]
                    });
                  }
                } else if (it.value) {
                  map.items.push({ start: [], key: null, sep: [this.sourceToken] });
                } else if (includesToken(it.sep, "map-value-ind")) {
                  this.stack.push({
                    type: "block-map",
                    offset: this.offset,
                    indent: this.indent,
                    items: [{ start, key: null, sep: [this.sourceToken] }]
                  });
                } else if (isFlowToken(it.key) && !includesToken(it.sep, "newline")) {
                  const start2 = getFirstKeyStartProps(it.start);
                  const key = it.key;
                  const sep = it.sep;
                  sep.push(this.sourceToken);
                  delete it.key;
                  delete it.sep;
                  this.stack.push({
                    type: "block-map",
                    offset: this.offset,
                    indent: this.indent,
                    items: [{ start: start2, key, sep }]
                  });
                } else if (start.length > 0) {
                  it.sep = it.sep.concat(start, this.sourceToken);
                } else {
                  it.sep.push(this.sourceToken);
                }
              } else {
                if (!it.sep) {
                  Object.assign(it, { key: null, sep: [this.sourceToken] });
                } else if (it.value || atNextItem) {
                  map.items.push({ start, key: null, sep: [this.sourceToken] });
                } else if (includesToken(it.sep, "map-value-ind")) {
                  this.stack.push({
                    type: "block-map",
                    offset: this.offset,
                    indent: this.indent,
                    items: [{ start: [], key: null, sep: [this.sourceToken] }]
                  });
                } else {
                  it.sep.push(this.sourceToken);
                }
              }
              this.onKeyLine = true;
              return;
            case "alias":
            case "scalar":
            case "single-quoted-scalar":
            case "double-quoted-scalar": {
              const fs = this.flowScalar(this.type);
              if (atNextItem || it.value) {
                map.items.push({ start, key: fs, sep: [] });
                this.onKeyLine = true;
              } else if (it.sep) {
                this.stack.push(fs);
              } else {
                Object.assign(it, { key: fs, sep: [] });
                this.onKeyLine = true;
              }
              return;
            }
            default: {
              const bv = this.startBlockValue(map);
              if (bv) {
                if (bv.type === "block-seq") {
                  if (!it.explicitKey && it.sep && !includesToken(it.sep, "newline")) {
                    yield* this.pop({
                      type: "error",
                      offset: this.offset,
                      message: "Unexpected block-seq-ind on same line with key",
                      source: this.source
                    });
                    return;
                  }
                } else if (atMapIndent) {
                  map.items.push({ start });
                }
                this.stack.push(bv);
                return;
              }
            }
          }
        }
        yield* this.pop();
        yield* this.step();
      }
      *blockSequence(seq) {
        const it = seq.items[seq.items.length - 1];
        switch (this.type) {
          case "newline":
            if (it.value) {
              const end = "end" in it.value ? it.value.end : void 0;
              const last = Array.isArray(end) ? end[end.length - 1] : void 0;
              if (last?.type === "comment")
                end?.push(this.sourceToken);
              else
                seq.items.push({ start: [this.sourceToken] });
            } else
              it.start.push(this.sourceToken);
            return;
          case "space":
          case "comment":
            if (it.value)
              seq.items.push({ start: [this.sourceToken] });
            else {
              if (this.atIndentedComment(it.start, seq.indent)) {
                const prev = seq.items[seq.items.length - 2];
                const end = prev?.value?.end;
                if (Array.isArray(end)) {
                  arrayPushArray(end, it.start);
                  end.push(this.sourceToken);
                  seq.items.pop();
                  return;
                }
              }
              it.start.push(this.sourceToken);
            }
            return;
          case "anchor":
          case "tag":
            if (it.value || this.indent <= seq.indent)
              break;
            it.start.push(this.sourceToken);
            return;
          case "seq-item-ind":
            if (this.indent !== seq.indent)
              break;
            if (it.value || includesToken(it.start, "seq-item-ind"))
              seq.items.push({ start: [this.sourceToken] });
            else
              it.start.push(this.sourceToken);
            return;
        }
        if (this.indent > seq.indent) {
          const bv = this.startBlockValue(seq);
          if (bv) {
            this.stack.push(bv);
            return;
          }
        }
        yield* this.pop();
        yield* this.step();
      }
      *flowCollection(fc) {
        const it = fc.items[fc.items.length - 1];
        if (this.type === "flow-error-end") {
          let top;
          do {
            yield* this.pop();
            top = this.peek(1);
          } while (top?.type === "flow-collection");
        } else if (fc.end.length === 0) {
          switch (this.type) {
            case "comma":
            case "explicit-key-ind":
              if (!it || it.sep)
                fc.items.push({ start: [this.sourceToken] });
              else
                it.start.push(this.sourceToken);
              return;
            case "map-value-ind":
              if (!it || it.value)
                fc.items.push({ start: [], key: null, sep: [this.sourceToken] });
              else if (it.sep)
                it.sep.push(this.sourceToken);
              else
                Object.assign(it, { key: null, sep: [this.sourceToken] });
              return;
            case "space":
            case "comment":
            case "newline":
            case "anchor":
            case "tag":
              if (!it || it.value)
                fc.items.push({ start: [this.sourceToken] });
              else if (it.sep)
                it.sep.push(this.sourceToken);
              else
                it.start.push(this.sourceToken);
              return;
            case "alias":
            case "scalar":
            case "single-quoted-scalar":
            case "double-quoted-scalar": {
              const fs = this.flowScalar(this.type);
              if (!it || it.value)
                fc.items.push({ start: [], key: fs, sep: [] });
              else if (it.sep)
                this.stack.push(fs);
              else
                Object.assign(it, { key: fs, sep: [] });
              return;
            }
            case "flow-map-end":
            case "flow-seq-end":
              fc.end.push(this.sourceToken);
              return;
          }
          const bv = this.startBlockValue(fc);
          if (bv)
            this.stack.push(bv);
          else {
            yield* this.pop();
            yield* this.step();
          }
        } else {
          const parent = this.peek(2);
          if (parent.type === "block-map" && (this.type === "map-value-ind" && parent.indent === fc.indent || this.type === "newline" && !parent.items[parent.items.length - 1].sep)) {
            yield* this.pop();
            yield* this.step();
          } else if (this.type === "map-value-ind" && parent.type !== "flow-collection") {
            const prev = getPrevProps(parent);
            const start = getFirstKeyStartProps(prev);
            fixFlowSeqItems(fc);
            const sep = fc.end.splice(1, fc.end.length);
            sep.push(this.sourceToken);
            const map = {
              type: "block-map",
              offset: fc.offset,
              indent: fc.indent,
              items: [{ start, key: fc, sep }]
            };
            this.onKeyLine = true;
            this.stack[this.stack.length - 1] = map;
          } else {
            yield* this.lineEnd(fc);
          }
        }
      }
      flowScalar(type) {
        if (this.onNewLine) {
          let nl = this.source.indexOf("\n") + 1;
          while (nl !== 0) {
            this.onNewLine(this.offset + nl);
            nl = this.source.indexOf("\n", nl) + 1;
          }
        }
        return {
          type,
          offset: this.offset,
          indent: this.indent,
          source: this.source
        };
      }
      startBlockValue(parent) {
        switch (this.type) {
          case "alias":
          case "scalar":
          case "single-quoted-scalar":
          case "double-quoted-scalar":
            return this.flowScalar(this.type);
          case "block-scalar-header":
            return {
              type: "block-scalar",
              offset: this.offset,
              indent: this.indent,
              props: [this.sourceToken],
              source: ""
            };
          case "flow-map-start":
          case "flow-seq-start":
            return {
              type: "flow-collection",
              offset: this.offset,
              indent: this.indent,
              start: this.sourceToken,
              items: [],
              end: []
            };
          case "seq-item-ind":
            return {
              type: "block-seq",
              offset: this.offset,
              indent: this.indent,
              items: [{ start: [this.sourceToken] }]
            };
          case "explicit-key-ind": {
            this.onKeyLine = true;
            const prev = getPrevProps(parent);
            const start = getFirstKeyStartProps(prev);
            start.push(this.sourceToken);
            return {
              type: "block-map",
              offset: this.offset,
              indent: this.indent,
              items: [{ start, explicitKey: true }]
            };
          }
          case "map-value-ind": {
            this.onKeyLine = true;
            const prev = getPrevProps(parent);
            const start = getFirstKeyStartProps(prev);
            return {
              type: "block-map",
              offset: this.offset,
              indent: this.indent,
              items: [{ start, key: null, sep: [this.sourceToken] }]
            };
          }
        }
        return null;
      }
      atIndentedComment(start, indent) {
        if (this.type !== "comment")
          return false;
        if (this.indent <= indent)
          return false;
        return start.every((st) => st.type === "newline" || st.type === "space");
      }
      *documentEnd(docEnd) {
        if (this.type !== "doc-mode") {
          if (docEnd.end)
            docEnd.end.push(this.sourceToken);
          else
            docEnd.end = [this.sourceToken];
          if (this.type === "newline")
            yield* this.pop();
        }
      }
      *lineEnd(token) {
        switch (this.type) {
          case "comma":
          case "doc-start":
          case "doc-end":
          case "flow-seq-end":
          case "flow-map-end":
          case "map-value-ind":
            yield* this.pop();
            yield* this.step();
            break;
          case "newline":
            this.onKeyLine = false;
          // fallthrough
          case "space":
          case "comment":
          default:
            if (token.end)
              token.end.push(this.sourceToken);
            else
              token.end = [this.sourceToken];
            if (this.type === "newline")
              yield* this.pop();
        }
      }
    };
    exports.Parser = Parser;
  }
});

// node_modules/yaml/dist/public-api.js
var require_public_api = __commonJS({
  "node_modules/yaml/dist/public-api.js"(exports) {
    "use strict";
    var composer = require_composer();
    var Document = require_Document();
    var errors = require_errors();
    var log = require_log();
    var identity = require_identity();
    var lineCounter = require_line_counter();
    var parser = require_parser();
    function parseOptions(options) {
      const prettyErrors = options.prettyErrors !== false;
      const lineCounter$1 = options.lineCounter || prettyErrors && new lineCounter.LineCounter() || null;
      return { lineCounter: lineCounter$1, prettyErrors };
    }
    function parseAllDocuments(source, options = {}) {
      const { lineCounter: lineCounter2, prettyErrors } = parseOptions(options);
      const parser$1 = new parser.Parser(lineCounter2?.addNewLine);
      const composer$1 = new composer.Composer(options);
      const docs = Array.from(composer$1.compose(parser$1.parse(source)));
      if (prettyErrors && lineCounter2)
        for (const doc of docs) {
          doc.errors.forEach(errors.prettifyError(source, lineCounter2));
          doc.warnings.forEach(errors.prettifyError(source, lineCounter2));
        }
      if (docs.length > 0)
        return docs;
      return Object.assign([], { empty: true }, composer$1.streamInfo());
    }
    function parseDocument(source, options = {}) {
      const { lineCounter: lineCounter2, prettyErrors } = parseOptions(options);
      const parser$1 = new parser.Parser(lineCounter2?.addNewLine);
      const composer$1 = new composer.Composer(options);
      let doc = null;
      for (const _doc of composer$1.compose(parser$1.parse(source), true, source.length)) {
        if (!doc)
          doc = _doc;
        else if (doc.options.logLevel !== "silent") {
          doc.errors.push(new errors.YAMLParseError(_doc.range.slice(0, 2), "MULTIPLE_DOCS", "Source contains multiple documents; please use YAML.parseAllDocuments()"));
          break;
        }
      }
      if (prettyErrors && lineCounter2) {
        doc.errors.forEach(errors.prettifyError(source, lineCounter2));
        doc.warnings.forEach(errors.prettifyError(source, lineCounter2));
      }
      return doc;
    }
    function parse3(src, reviver, options) {
      let _reviver = void 0;
      if (typeof reviver === "function") {
        _reviver = reviver;
      } else if (options === void 0 && reviver && typeof reviver === "object") {
        options = reviver;
      }
      const doc = parseDocument(src, options);
      if (!doc)
        return null;
      doc.warnings.forEach((warning) => log.warn(doc.options.logLevel, warning));
      if (doc.errors.length > 0) {
        if (doc.options.logLevel !== "silent")
          throw doc.errors[0];
        else
          doc.errors = [];
      }
      return doc.toJS(Object.assign({ reviver: _reviver }, options));
    }
    function stringify(value, replacer, options) {
      let _replacer = null;
      if (typeof replacer === "function" || Array.isArray(replacer)) {
        _replacer = replacer;
      } else if (options === void 0 && replacer) {
        options = replacer;
      }
      if (typeof options === "string")
        options = options.length;
      if (typeof options === "number") {
        const indent = Math.round(options);
        options = indent < 1 ? void 0 : indent > 8 ? { indent: 8 } : { indent };
      }
      if (value === void 0) {
        const { keepUndefined } = options ?? replacer ?? {};
        if (!keepUndefined)
          return void 0;
      }
      if (identity.isDocument(value) && !_replacer)
        return value.toString(options);
      return new Document.Document(value, _replacer, options).toString(options);
    }
    exports.parse = parse3;
    exports.parseAllDocuments = parseAllDocuments;
    exports.parseDocument = parseDocument;
    exports.stringify = stringify;
  }
});

// node_modules/yaml/dist/index.js
var require_dist = __commonJS({
  "node_modules/yaml/dist/index.js"(exports) {
    "use strict";
    var composer = require_composer();
    var Document = require_Document();
    var Schema = require_Schema();
    var errors = require_errors();
    var Alias = require_Alias();
    var identity = require_identity();
    var Pair = require_Pair();
    var Scalar = require_Scalar();
    var YAMLMap = require_YAMLMap();
    var YAMLSeq = require_YAMLSeq();
    var cst = require_cst();
    var lexer = require_lexer();
    var lineCounter = require_line_counter();
    var parser = require_parser();
    var publicApi = require_public_api();
    var visit = require_visit();
    exports.Composer = composer.Composer;
    exports.Document = Document.Document;
    exports.Schema = Schema.Schema;
    exports.YAMLError = errors.YAMLError;
    exports.YAMLParseError = errors.YAMLParseError;
    exports.YAMLWarning = errors.YAMLWarning;
    exports.Alias = Alias.Alias;
    exports.isAlias = identity.isAlias;
    exports.isCollection = identity.isCollection;
    exports.isDocument = identity.isDocument;
    exports.isMap = identity.isMap;
    exports.isNode = identity.isNode;
    exports.isPair = identity.isPair;
    exports.isScalar = identity.isScalar;
    exports.isSeq = identity.isSeq;
    exports.Pair = Pair.Pair;
    exports.Scalar = Scalar.Scalar;
    exports.YAMLMap = YAMLMap.YAMLMap;
    exports.YAMLSeq = YAMLSeq.YAMLSeq;
    exports.CST = cst;
    exports.Lexer = lexer.Lexer;
    exports.LineCounter = lineCounter.LineCounter;
    exports.Parser = parser.Parser;
    exports.parse = publicApi.parse;
    exports.parseAllDocuments = publicApi.parseAllDocuments;
    exports.parseDocument = publicApi.parseDocument;
    exports.stringify = publicApi.stringify;
    exports.visit = visit.visit;
    exports.visitAsync = visit.visitAsync;
  }
});

// src/cli/main.mjs
import * as fsPromises13 from "node:fs/promises";

// src/core/errors.mjs
var GatedLoopError = class extends Error {
  constructor(code, message, { exitCode = 1, details = {} } = {}) {
    super(message);
    this.name = "GatedLoopError";
    this.code = code;
    this.exitCode = exitCode;
    this.details = details;
  }
};

// src/mode/signals.mjs
import path2 from "node:path";

// src/core/hash.mjs
import { createHash } from "node:crypto";
import path from "node:path";
function canonicalRelativePath(value) {
  const portable = String(value).replaceAll("\\", "/");
  const normalized = path.posix.normalize(portable).replace(/^\.\//, "");
  if (normalized === ".." || normalized.startsWith("../") || path.posix.isAbsolute(normalized)) {
    throw new GatedLoopError("PATH_OUTSIDE_ROOT", `Path escapes root: ${value}`);
  }
  return normalized;
}
function sha256Bytes(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}
function manifestFingerprint(entries) {
  const canonical = entries.map(({ path: filePath, sha256 }) => ({ path: canonicalRelativePath(filePath), sha256 })).sort((a, b) => a.path.localeCompare(b.path));
  return sha256Bytes(Buffer.from(JSON.stringify(canonical), "utf8"));
}

// src/mode/signals.mjs
var BOOLEAN_FIELDS = Object.freeze([
  "loadBearing",
  "breaking",
  "dependencyChange",
  "newDependency",
  "externalContract",
  "permissions",
  "authentication",
  "stateMachine",
  "transaction",
  "concurrency",
  "idempotency",
  "thresholdDecision"
]);
var INPUT_FIELDS = /* @__PURE__ */ new Set([
  "description",
  "modifiesFiles",
  "writesFiles",
  ...BOOLEAN_FIELDS,
  "migrations",
  "unresolvedOptions",
  "impactKnown",
  "requestedMode"
]);
var MIGRATION_CATEGORIES = /* @__PURE__ */ new Set(["unspecified", "database", "schema", "data", "config", "storage", "api-version", "dependency"]);
var CONTRACT_FILE_EXTENSIONS = /* @__PURE__ */ new Set([
  "avsc",
  "graphql",
  "graphqls",
  "json",
  "jsonc",
  "md",
  "mdx",
  "proto",
  "sql",
  "prisma",
  "toml",
  "wsdl",
  "xsd",
  "yaml",
  "yml"
]);
var INTRINSIC_CONTRACT_EXTENSIONS = /* @__PURE__ */ new Set([
  "avdl",
  "avsc",
  "graphqls",
  "prisma",
  "proto",
  "raml",
  "thrift",
  "wsdl",
  "xsd"
]);
var DIRECTORY_CONTRACT_PATTERN = /^(?:open[._-]?api|swagger|specs?|specifications?|schemas?|public[._-](?:contracts?|apis?))(?:[._-]?v?\d+(?:[._-]\d+)*)?$/;
var STEM_CONTRACT_PATTERN = /(?:^|[._-])(?:open[._-]?api|swagger|specs?|specifications?|schemas?|public[._-]+(?:contracts?|apis?))(?:[._-]?v?\d+(?:[._-]\d+)*)?$/;
function invalid(message, details = {}) {
  throw new GatedLoopError("MODE_SIGNALS_INVALID", message, { details });
}
function normalizePath(value) {
  if (typeof value !== "string" || value.length === 0 || /[\u0000-\u001F\u007F]/.test(value) || /[*?\[\]{}<>"|]/.test(value) || path2.posix.isAbsolute(value) || path2.win32.isAbsolute(value) || /^[\\/]{2}/.test(value) || value.includes(":")) invalid("modifiesFiles must contain safe relative paths", { path: value });
  let normalized;
  try {
    normalized = canonicalRelativePath(value);
  } catch {
    invalid("modifiesFiles must contain safe relative paths", { path: value });
  }
  if (normalized === "." || normalized.split("/").includes("..")) invalid("modifiesFiles must contain safe relative paths", { path: value });
  return normalized;
}
function normalizeMigrations(value) {
  let values;
  if (value === void 0 || value === false || value === null) values = [];
  else if (value === true) values = ["unspecified"];
  else if (typeof value === "string") values = [value];
  else if (Array.isArray(value)) values = value;
  else if (typeof value === "object") {
    const entries = Object.entries(value);
    if (entries.some(([category]) => !MIGRATION_CATEGORIES.has(category))) {
      invalid("migrations contains an unknown category", { migrations: entries.map(([category]) => category) });
    }
    if (entries.some(([, enabled]) => typeof enabled !== "boolean")) invalid("migrations flags must be booleans");
    values = entries.filter(([, enabled]) => enabled).map(([category]) => category);
  } else invalid("migrations must be a boolean, category, array, or flag mapping");
  if (values.some((category) => typeof category !== "string" || !MIGRATION_CATEGORIES.has(category))) {
    invalid("migrations contains an unknown category", { migrations: values });
  }
  return [...new Set(values)].sort();
}
function isLoadBearingPath(filePath) {
  const normalized = normalizePath(filePath);
  const lower = normalized.toLowerCase();
  const segments = lower.split("/");
  const basename = segments.at(-1);
  if (["skill.md", "agents.md", "claude.md"].includes(basename)) return true;
  if (segments.some((segment) => DIRECTORY_CONTRACT_PATTERN.test(segment))) return true;
  const extension = basename.includes(".") ? basename.split(".").at(-1) : "";
  if (INTRINSIC_CONTRACT_EXTENSIONS.has(extension) || basename === "schema.rb") return true;
  if (!CONTRACT_FILE_EXTENSIONS.has(extension)) return false;
  const stem = basename.slice(0, -(extension.length + 1));
  return STEM_CONTRACT_PATTERN.test(stem);
}
function normalizeSignals(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) invalid("Mode signals must be a mapping");
  for (const key of Object.keys(input)) if (!INPUT_FIELDS.has(key)) invalid(`Unknown mode signal: ${key}`, { key });
  if (input.description !== void 0 && typeof input.description !== "string") invalid("description must be a string");
  if (input.modifiesFiles !== void 0 && (!Array.isArray(input.modifiesFiles) || input.modifiesFiles.some((entry) => typeof entry !== "string"))) {
    invalid("modifiesFiles must be an array of strings");
  }
  if (input.writesFiles !== void 0 && typeof input.writesFiles !== "boolean") invalid("writesFiles must be a boolean");
  for (const field of BOOLEAN_FIELDS) {
    if (input[field] !== void 0 && typeof input[field] !== "boolean") invalid(`${field} must be a boolean`, { field });
  }
  if (input.impactKnown !== void 0 && typeof input.impactKnown !== "boolean") invalid("impactKnown must be a boolean");
  if (input.unresolvedOptions !== void 0 && (!Number.isInteger(input.unresolvedOptions) || input.unresolvedOptions < 0)) {
    invalid("unresolvedOptions must be a non-negative integer");
  }
  if (input.requestedMode !== void 0 && input.requestedMode !== null && !["full", "light"].includes(input.requestedMode)) {
    invalid("requestedMode must be full, light, or null");
  }
  const modifiesFiles = [...new Set((input.modifiesFiles ?? []).map(normalizePath))].sort();
  const detectedLoadBearing = modifiesFiles.some(isLoadBearingPath);
  return {
    description: input.description ?? "",
    modifiesFiles,
    writesFiles: modifiesFiles.length > 0 || (input.writesFiles ?? true),
    loadBearing: Boolean(input.loadBearing) || detectedLoadBearing,
    breaking: Boolean(input.breaking),
    migrations: normalizeMigrations(input.migrations),
    dependencyChange: Boolean(input.dependencyChange),
    newDependency: Boolean(input.newDependency),
    externalContract: Boolean(input.externalContract),
    permissions: Boolean(input.permissions),
    authentication: Boolean(input.authentication),
    stateMachine: Boolean(input.stateMachine),
    transaction: Boolean(input.transaction),
    concurrency: Boolean(input.concurrency),
    idempotency: Boolean(input.idempotency),
    unresolvedOptions: input.unresolvedOptions ?? 0,
    thresholdDecision: Boolean(input.thresholdDecision),
    impactKnown: input.impactKnown ?? false,
    requestedMode: input.requestedMode ?? null
  };
}
var migrationCategories = Object.freeze([...MIGRATION_CATEGORIES]);

// src/mode/classify.mjs
var CLASSIFIER_VERSION = 1;
function hardReasons(input) {
  const reasons = [];
  if (input.loadBearing) reasons.push("LOAD_BEARING_FILE");
  if (input.breaking) reasons.push("BREAKING_CHANGE");
  if (input.migrations.length > 0) reasons.push("MIGRATION");
  if (input.dependencyChange) reasons.push("DEPENDENCY_CHANGE");
  if (input.newDependency) reasons.push("NEW_DEPENDENCY");
  if (input.externalContract) reasons.push("EXTERNAL_CONTRACT");
  if (input.permissions) reasons.push("PERMISSIONS");
  if (input.authentication) reasons.push("AUTHENTICATION");
  if (input.stateMachine) reasons.push("STATE_MACHINE");
  if (input.transaction) reasons.push("TRANSACTION");
  if (input.concurrency) reasons.push("CONCURRENCY");
  if (input.idempotency) reasons.push("IDEMPOTENCY");
  if (input.unresolvedOptions > 1) reasons.push("UNRESOLVED_OPTIONS");
  if (input.thresholdDecision) reasons.push("THRESHOLD_DECISION");
  const nonLoadBearingCount = input.modifiesFiles.filter((filePath) => !isLoadBearingPath(filePath)).length;
  if (nonLoadBearingCount > 3) reasons.push("FILE_LIMIT_EXCEEDED");
  if (input.writesFiles && input.modifiesFiles.length === 0) reasons.push("WRITE_PATHS_UNKNOWN");
  if (!input.impactKnown) reasons.push("IMPACT_UNKNOWN");
  return [...new Set(reasons)].sort();
}
function result(mode, reasons, confidence, evaluatedInputs) {
  return { mode, reasons, confidence, evaluatedInputs };
}
function classifyMode(signals) {
  const evaluatedInputs = normalizeSignals(signals);
  const reasons = hardReasons(evaluatedInputs);
  if (evaluatedInputs.requestedMode === "light" && reasons.length > 0) {
    throw new GatedLoopError("MODE_ESCALATION_REQUIRED", "Light mode cannot bypass Full mode requirements", {
      details: { requiredMode: "full", reasons }
    });
  }
  if (evaluatedInputs.requestedMode === "full") {
    const forcedReasons = [.../* @__PURE__ */ new Set([...reasons, "USER_FORCED_FULL"])].sort();
    return result("full", forcedReasons, "high", evaluatedInputs);
  }
  if (reasons.length > 0) {
    const uncertain = /* @__PURE__ */ new Set(["IMPACT_UNKNOWN", "WRITE_PATHS_UNKNOWN"]);
    const confidence = reasons.every((reason) => uncertain.has(reason)) ? "medium" : "high";
    return result("full", reasons, confidence, evaluatedInputs);
  }
  if (!evaluatedInputs.writesFiles) return result("none", ["NO_FILE_WRITES"], "high", evaluatedInputs);
  return result("light", ["LIGHT_ELIGIBLE"], "high", evaluatedInputs);
}

// src/commands/route.mjs
function routeTask(signals) {
  return classifyMode(signals);
}

// src/commands/start.mjs
import * as fsPromises5 from "node:fs/promises";

// src/core/fs-safe.mjs
import * as fsPromises from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import path3 from "node:path";
import { createHash as createHash2, randomBytes } from "node:crypto";
import { AsyncLocalStorage } from "node:async_hooks";
var RUNTIME_TRANSACTION_CONTEXT = new AsyncLocalStorage();
var PRESERVE_RUNTIME_LOCK = /* @__PURE__ */ Symbol("preserveRuntimeLock");
function contained(root, target, pathApi) {
  const relative = pathApi.relative(root, target);
  return relative === "" || !relative.startsWith(`..${pathApi.sep}`) && relative !== ".." && !pathApi.isAbsolute(relative);
}
async function assertSafePath(root, candidate, { fs = fsPromises, pathApi = path3 } = {}) {
  const rootAbsolute = pathApi.resolve(root);
  const candidateText = String(candidate);
  const target = pathApi.isAbsolute(candidateText) ? pathApi.resolve(candidateText) : pathApi.resolve(rootAbsolute, candidateText);
  if (pathApi.parse(rootAbsolute).root.toLowerCase() !== pathApi.parse(target).root.toLowerCase()) {
    throw new GatedLoopError("PATH_CROSS_VOLUME", `Path is on another volume: ${candidate}`);
  }
  if (!contained(rootAbsolute, target, pathApi)) throw new GatedLoopError("PATH_OUTSIDE_ROOT", `Path escapes root: ${candidate}`);
  let rootReal;
  try {
    const rootStat = await fs.lstat(rootAbsolute);
    if (rootStat.isSymbolicLink()) throw new GatedLoopError("PATH_SYMLINK", `Symbolic link is not allowed: ${rootAbsolute}`);
    rootReal = await fs.realpath(rootAbsolute);
  } catch (error) {
    if (error.code === "ENOENT") rootReal = rootAbsolute;
    else throw error;
  }
  const relative = pathApi.relative(rootAbsolute, target);
  const parts = relative ? relative.split(pathApi.sep) : [];
  let current = rootAbsolute;
  for (const part of parts) {
    current = pathApi.join(current, part);
    try {
      const stat = await fs.lstat(current);
      if (stat.isSymbolicLink()) throw new GatedLoopError("PATH_SYMLINK", `Symbolic link is not allowed: ${current}`);
      const real = await fs.realpath(current);
      if (!contained(rootReal, real, pathApi)) throw new GatedLoopError("PATH_OUTSIDE_ROOT", `Real path escapes root: ${current}`);
    } catch (error) {
      if (error.code === "ENOENT") break;
      throw error;
    }
  }
  return target;
}
function fileChanged(target) {
  throw new GatedLoopError("PATH_FILE_CHANGED", `File changed while it was being opened: ${target}`);
}
function sameIdentity(left, right) {
  const valid = (value) => (typeof value === "number" || typeof value === "bigint") && value !== 0 && value !== 0n;
  return valid(left?.ino) && valid(right?.ino) && left.dev === right.dev && left.ino === right.ino;
}
function sameSnapshot(left, right) {
  const leftMtime = left?.mtimeNs ?? left?.mtimeMs;
  const rightMtime = right?.mtimeNs ?? right?.mtimeMs;
  const leftCtime = left?.ctimeNs ?? left?.ctimeMs;
  const rightCtime = right?.ctimeNs ?? right?.ctimeMs;
  return sameIdentity(left, right) && left.mode === right.mode && left.size === right.size && leftMtime === rightMtime && leftCtime === rightCtime;
}
function sameFileSnapshot(left, right) {
  return sameSnapshot(left, right);
}
async function readSafeRegularFileSnapshot(root, candidate, { fs = fsPromises, pathApi = path3 } = {}) {
  const target = await assertSafePath(root, candidate, { fs, pathApi });
  const before = await fs.lstat(target, { bigint: true });
  if (before.isSymbolicLink() || !before.isFile()) fileChanged(target);
  let handle;
  try {
    const flags = fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0);
    handle = await fs.open(target, flags);
    const opened = await handle.stat({ bigint: true });
    if (!opened.isFile() || !sameSnapshot(before, opened)) fileChanged(target);
    await assertSafePath(root, candidate, { fs, pathApi });
    const stillLinked = await fs.lstat(target, { bigint: true });
    if (stillLinked.isSymbolicLink() || !sameSnapshot(opened, stillLinked)) fileChanged(target);
    const bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    const finalLink = await fs.lstat(target, { bigint: true });
    if (!sameSnapshot(opened, after) || finalLink.isSymbolicLink() || !sameSnapshot(after, finalLink)) fileChanged(target);
    return { bytes, snapshot: after };
  } finally {
    await handle?.close();
  }
}
async function readSafeRegularFile(root, candidate, options = {}) {
  return (await readSafeRegularFileSnapshot(root, candidate, options)).bytes;
}
function stagingName(target) {
  return `${target}.tmp-${process.pid}-${randomBytes(6).toString("hex")}`;
}
function runtimeLockText(value) {
  return `${JSON.stringify(value)}
`;
}
async function observedRuntimeRecovery(descriptor, fs) {
  try {
    const parsed = JSON.parse(await fs.readFile(descriptor.recoveryPath, "utf8"));
    return { present: true, record: parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null };
  } catch (error) {
    if (error.code === "ENOENT") return { present: false, record: null };
    return { present: true, record: null };
  }
}
function validRecoveryTransaction(descriptor, transaction, expectedToken) {
  return transaction && typeof transaction === "object" && !Array.isArray(transaction) && transaction.version === 1 && typeof transaction.token === "string" && (!expectedToken || transaction.token === expectedToken) && transaction.identity === descriptor.identity && transaction.original === descriptor.target && typeof transaction.phase === "string" && typeof transaction.staging === "string" && (transaction.backup === null || typeof transaction.backup === "string") && typeof transaction.originalExisted === "boolean";
}
async function recoveryDetails(descriptor, fs, record, observedRecord = record, knownTransaction) {
  const owner = observedRecord && typeof observedRecord === "object" ? {
    pid: observedRecord.ownerPid,
    token: observedRecord.token,
    acquiredAt: observedRecord.acquiredAt
  } : null;
  const expectedToken = observedRecord?.token ?? record?.token;
  const observedRecovery = knownTransaction ? { present: true, record: knownTransaction } : await observedRuntimeRecovery(descriptor, fs).catch(() => ({ present: true, record: null }));
  const transaction = validRecoveryTransaction(descriptor, observedRecovery.record, expectedToken) ? observedRecovery.record : null;
  return {
    automaticRecovery: false,
    recoveryRequired: true,
    lockPath: descriptor.lockPath,
    recoveryPath: descriptor.recoveryPath,
    runtimeDirectory: descriptor.target,
    owner,
    transaction,
    artifactPatterns: [
      `${descriptor.target}.tmp-*`,
      `${descriptor.target}.backup.tmp-*`
    ]
  };
}
async function observedRuntimeLock(descriptor, fs) {
  try {
    return JSON.parse(await fs.readFile(descriptor.lockPath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") throw error;
    return null;
  }
}
async function operationInProgress(descriptor, fs, observedRecord) {
  return new GatedLoopError("OPERATION_IN_PROGRESS", "Another runtime-directory operation is already active", {
    details: { recovery: await recoveryDetails(descriptor, fs, void 0, observedRecord) }
  });
}
async function runtimeTransactionLock(target, {
  fs = fsPromises,
  pathApi = path3,
  platform = process.platform,
  createParent = true
} = {}) {
  const lexicalTarget = pathApi.resolve(target);
  const parent = pathApi.dirname(lexicalTarget);
  if (createParent) await fs.mkdir(parent, { recursive: true });
  const parentStat = await fs.lstat(parent);
  if (!parentStat.isDirectory() || parentStat.isSymbolicLink()) {
    throw new GatedLoopError("ATOMIC_TARGET_INVALID", "Runtime directory parent is invalid");
  }
  const realParent = await fs.realpath(parent);
  const canonicalTarget = pathApi.join(realParent, pathApi.basename(lexicalTarget));
  const normalized = pathApi.resolve(canonicalTarget).normalize("NFC");
  const identity = platform === "win32" ? normalized.toLowerCase() : normalized;
  const hash = createHash2("sha256").update(identity).digest("hex").slice(0, 24);
  const lockPath = pathApi.join(realParent, `.gated-loop-runtime-${hash}.lock`);
  return {
    identity,
    target: canonicalTarget,
    lockPath,
    recoveryPath: `${lockPath}.recovery.json`
  };
}
async function restoreClaimWithoutReplacingSuccessor(claimedPath, originalPath, fs) {
  try {
    await fs.link(claimedPath, originalPath);
  } catch {
    return;
  }
  await fs.rm(claimedPath).catch(() => {
  });
}
async function claimOwnedRuntimeFile(filePath, token, isOwned, fs) {
  let before;
  let observed = null;
  try {
    before = await fs.lstat(filePath, { bigint: true });
    if (before.isSymbolicLink() || !before.isFile()) return { status: "lost", observed };
    observed = JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return { status: "lost", observed: null };
    if (error instanceof SyntaxError) return { status: "lost", observed: null };
    return { status: "cleanup-failed", error, observed };
  }
  if (!isOwned(observed)) return { status: "lost", observed };
  const claimedPath = `${filePath}.release-${process.pid}-${token}-${randomBytes(4).toString("hex")}`;
  try {
    await fs.rename(filePath, claimedPath);
  } catch (error) {
    if (error.code === "ENOENT") return { status: "lost", observed: null };
    return { status: "cleanup-failed", error, observed };
  }
  let claimedStat;
  let claimedRecord = null;
  try {
    claimedStat = await fs.lstat(claimedPath, { bigint: true });
    claimedRecord = JSON.parse(await fs.readFile(claimedPath, "utf8"));
  } catch (error) {
    await restoreClaimWithoutReplacingSuccessor(claimedPath, filePath, fs);
    if (error.code === "ENOENT" || error instanceof SyntaxError) {
      return { status: "lost", observed: claimedRecord };
    }
    return { status: "cleanup-failed", error, observed: claimedRecord };
  }
  if (!sameIdentity(before, claimedStat) || !isOwned(claimedRecord)) {
    await restoreClaimWithoutReplacingSuccessor(claimedPath, filePath, fs);
    return { status: "lost", observed: claimedRecord };
  }
  try {
    await fs.rm(claimedPath);
  } catch (error) {
    await restoreClaimWithoutReplacingSuccessor(claimedPath, filePath, fs);
    return { status: "cleanup-failed", error, observed: claimedRecord };
  }
  return { status: "removed", observed: claimedRecord };
}
async function releaseRuntimeTransaction(descriptor, record, fs) {
  const result3 = await claimOwnedRuntimeFile(
    descriptor.lockPath,
    record.token,
    (observed) => observed?.token === record.token && observed?.identity === record.identity,
    fs
  );
  if (result3.status === "lost") {
    throw new GatedLoopError("OPERATION_LOCK_OWNERSHIP_LOST", "Runtime operation lock is no longer owned by this token", {
      details: { recovery: await recoveryDetails(descriptor, fs, record, result3.observed) }
    });
  }
  if (result3.status === "cleanup-failed") {
    throw new GatedLoopError("OPERATION_LOCK_CLEANUP_FAILED", "Unable to remove the owned runtime operation lock", {
      details: {
        cleanupCode: result3.error?.code,
        recovery: await recoveryDetails(descriptor, fs, record)
      }
    });
  }
}
function activeRuntimeTransaction(descriptor) {
  return RUNTIME_TRANSACTION_CONTEXT.getStore()?.get(descriptor.identity);
}
function preserveRuntimeLock(error) {
  if (error && (typeof error === "object" || typeof error === "function")) error[PRESERVE_RUNTIME_LOCK] = true;
  return error;
}
async function runtimeOwnershipLost(descriptor, record, fs, observed = null) {
  return new GatedLoopError("OPERATION_LOCK_OWNERSHIP_LOST", "Runtime operation lock is no longer owned by this token", {
    details: { recovery: await recoveryDetails(descriptor, fs, record, observed) }
  });
}
async function assertRuntimeTransactionOwner(transaction, fs) {
  let observed;
  try {
    observed = await observedRuntimeLock(transaction.descriptor, fs);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    throw await runtimeOwnershipLost(transaction.descriptor, transaction.record, fs, null);
  }
  if (observed?.token !== transaction.record.token || observed?.identity !== transaction.record.identity) {
    throw await runtimeOwnershipLost(transaction.descriptor, transaction.record, fs, observed);
  }
}
function recoveryTransaction(transaction, { phase, staging, backup, originalExisted }) {
  return {
    version: 1,
    token: transaction.record.token,
    identity: transaction.descriptor.identity,
    original: transaction.descriptor.target,
    phase,
    staging,
    backup,
    originalExisted
  };
}
async function beginRuntimeRecovery(transaction, details, fs) {
  const recovery = recoveryTransaction(transaction, details);
  try {
    await fs.writeFile(
      transaction.descriptor.recoveryPath,
      runtimeLockText(recovery),
      { encoding: "utf8", flag: "wx" }
    );
  } catch (error) {
    if (error.code === "EEXIST") {
      throw preserveRuntimeLock(await operationInProgress(transaction.descriptor, fs, transaction.record));
    }
    throw error;
  }
  transaction.recovery = recovery;
  return recovery;
}
async function updateRuntimeRecovery(transaction, phase, fs) {
  const recovery = { ...transaction.recovery, phase };
  try {
    await atomicWriteFile(transaction.descriptor.recoveryPath, runtimeLockText(recovery), { fs });
  } catch (error) {
    throw preserveRuntimeLock(error);
  }
  transaction.recovery = recovery;
  return recovery;
}
async function removeRuntimeRecovery(transaction, fs) {
  const result3 = await claimOwnedRuntimeFile(
    transaction.descriptor.recoveryPath,
    transaction.record.token,
    (observed) => validRecoveryTransaction(
      transaction.descriptor,
      observed,
      transaction.record.token
    ),
    fs
  );
  if (result3.status === "removed") {
    transaction.recovery = null;
    return;
  }
  const code = result3.status === "lost" ? "OPERATION_RECOVERY_OWNERSHIP_LOST" : "OPERATION_RECOVERY_CLEANUP_FAILED";
  const message = result3.status === "lost" ? "Runtime recovery journal is no longer owned by this token" : "Unable to remove the owned runtime recovery journal";
  throw preserveRuntimeLock(new GatedLoopError(code, message, {
    details: {
      cleanupCode: result3.error?.code,
      recovery: await recoveryDetails(
        transaction.descriptor,
        fs,
        transaction.record,
        transaction.record,
        transaction.recovery
      )
    }
  }));
}
async function withRuntimeDirectoryTransaction(target, operation, {
  fs = fsPromises,
  pathApi = path3,
  platform = process.platform,
  now = () => /* @__PURE__ */ new Date()
} = {}) {
  if (typeof operation !== "function") throw new TypeError("Runtime transaction operation must be a function");
  const descriptor = await runtimeTransactionLock(target, { fs, pathApi, platform });
  const priorRecovery = await observedRuntimeRecovery(descriptor, fs);
  if (priorRecovery.present) {
    const observed = await observedRuntimeLock(descriptor, fs).catch(() => null);
    throw await operationInProgress(descriptor, fs, observed);
  }
  const token = randomBytes(12).toString("hex");
  const acquired = typeof now === "function" ? now() : now;
  const acquiredAt = (acquired instanceof Date ? acquired : new Date(acquired)).toISOString();
  const record = {
    version: 1,
    target: pathApi.basename(descriptor.target),
    identity: descriptor.identity,
    ownerPid: process.pid,
    token,
    acquiredAt
  };
  try {
    await fs.writeFile(descriptor.lockPath, runtimeLockText(record), { encoding: "utf8", flag: "wx" });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    const observed = await observedRuntimeLock(descriptor, fs).catch(() => null);
    throw await operationInProgress(descriptor, fs, observed);
  }
  const racedRecovery = await observedRuntimeRecovery(descriptor, fs);
  if (racedRecovery.present) {
    const blocked = await operationInProgress(descriptor, fs, record);
    await releaseRuntimeTransaction(descriptor, record, fs);
    throw blocked;
  }
  const inherited = RUNTIME_TRANSACTION_CONTEXT.getStore() ?? /* @__PURE__ */ new Map();
  const context = new Map(inherited);
  context.set(descriptor.identity, { descriptor, record });
  let preserve = false;
  try {
    return await RUNTIME_TRANSACTION_CONTEXT.run(
      context,
      () => operation({ ...descriptor, token, record })
    );
  } catch (error) {
    preserve = error?.[PRESERVE_RUNTIME_LOCK] === true;
    throw error;
  } finally {
    if (!preserve) await releaseRuntimeTransaction(descriptor, record, fs);
  }
}
async function resolveAtomicDirectory(target, {
  fs = fsPromises,
  pathApi = path3,
  platform = process.platform
} = {}) {
  const descriptor = await runtimeTransactionLock(target, {
    fs,
    pathApi,
    platform,
    createParent: false
  });
  let observed;
  let lockPresent = false;
  try {
    observed = await observedRuntimeLock(descriptor, fs);
    lockPresent = true;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const active = activeRuntimeTransaction(descriptor);
  if (lockPresent) {
    if (!observed || active?.record.token !== observed.token || active.record.identity !== observed.identity) {
      throw await operationInProgress(descriptor, fs, observed);
    }
    const recovery = await observedRuntimeRecovery(descriptor, fs);
    if (recovery.present && !validRecoveryTransaction(descriptor, recovery.record, active.record.token)) {
      throw await operationInProgress(descriptor, fs, observed);
    }
  } else {
    const recovery = await observedRuntimeRecovery(descriptor, fs);
    if (active) throw await runtimeOwnershipLost(descriptor, active.record, fs, null);
    if (recovery.present) throw await operationInProgress(descriptor, fs, null);
  }
  const stat = await fs.lstat(descriptor.target);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new GatedLoopError("ATOMIC_TARGET_INVALID", "Atomic directory target is invalid");
  }
  return descriptor.target;
}
async function atomicWriteFile(target, content2, { fs = fsPromises, beforeRename } = {}) {
  const staging = stagingName(target);
  await fs.mkdir(path3.dirname(target), { recursive: true });
  try {
    await fs.writeFile(staging, content2, { encoding: "utf8", flag: "wx" });
    if (beforeRename) await beforeRename(staging);
    await fs.rename(staging, target);
  } catch (error) {
    await fs.rm(staging, { force: true }).catch(() => {
    });
    throw error;
  }
}
async function atomicWriteDirectory(target, populate, {
  fs = fsPromises,
  pathApi = path3,
  platform = process.platform
} = {}) {
  const descriptor = await runtimeTransactionLock(target, { fs, pathApi, platform });
  const active = activeRuntimeTransaction(descriptor);
  const write = async (transaction) => {
    const lockedTarget = transaction.descriptor.target;
    const staging = stagingName(lockedTarget);
    let recoveryStarted = false;
    let installed = false;
    await assertRuntimeTransactionOwner(transaction, fs);
    let existing;
    try {
      existing = await fs.lstat(lockedTarget);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (existing) {
      const error = new Error("Atomic directory target already exists");
      error.code = "EEXIST";
      throw error;
    }
    await beginRuntimeRecovery(transaction, {
      phase: "staging",
      staging,
      backup: null,
      originalExisted: false
    }, fs);
    recoveryStarted = true;
    try {
      await fs.mkdir(staging);
      await populate(staging);
      await updateRuntimeRecovery(transaction, "staged", fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      await updateRuntimeRecovery(transaction, "commit-pending", fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      await fs.rename(staging, lockedTarget);
      installed = true;
      await updateRuntimeRecovery(transaction, "installed", fs);
      await removeRuntimeRecovery(transaction, fs);
    } catch (error) {
      if (!installed) {
        await fs.rm(staging, { recursive: true, force: true }).catch(() => {
        });
        if (recoveryStarted) {
          try {
            await removeRuntimeRecovery(transaction, fs);
          } catch (cleanupError) {
            throw cleanupError;
          }
        }
        if (error && (typeof error === "object" || typeof error === "function")) {
          delete error[PRESERVE_RUNTIME_LOCK];
        }
      } else {
        preserveRuntimeLock(error);
      }
      throw error;
    }
  };
  if (active) return write(active);
  return withRuntimeDirectoryTransaction(
    descriptor.target,
    ({ record, ...ownedDescriptor }) => write({ descriptor: ownedDescriptor, record }),
    { fs, pathApi, platform }
  );
}
async function atomicReplaceDirectory(target, populate, {
  fs = fsPromises,
  beforeSwap,
  validateUnderLock,
  pathApi = path3,
  platform = process.platform
} = {}) {
  const descriptor = await runtimeTransactionLock(target, { fs, pathApi, platform });
  const replace = async (transaction) => {
    const lockedTarget = transaction.descriptor.target;
    const staging = stagingName(lockedTarget);
    let backup = null;
    let movedExisting = false;
    let installed = false;
    let recoveryStarted = false;
    await assertRuntimeTransactionOwner(transaction, fs);
    let existing;
    try {
      existing = await fs.lstat(lockedTarget);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (existing && (!existing.isDirectory() || existing.isSymbolicLink())) {
      throw new GatedLoopError("ATOMIC_TARGET_INVALID", "Atomic directory target is invalid");
    }
    if (existing) backup = stagingName(`${lockedTarget}.backup`);
    await beginRuntimeRecovery(transaction, {
      phase: "staging",
      staging,
      backup,
      originalExisted: Boolean(existing)
    }, fs);
    recoveryStarted = true;
    try {
      await fs.mkdir(staging);
      await populate(staging);
      if (beforeSwap) await beforeSwap(staging);
      if (validateUnderLock) await validateUnderLock(staging);
      await updateRuntimeRecovery(transaction, "staged", fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      await updateRuntimeRecovery(transaction, "commit-pending", fs);
      await assertRuntimeTransactionOwner(transaction, fs);
      if (existing) {
        await fs.rename(lockedTarget, backup);
        movedExisting = true;
        await updateRuntimeRecovery(transaction, "original-moved", fs);
      }
      await fs.rename(staging, lockedTarget);
      installed = true;
      await updateRuntimeRecovery(transaction, "installed", fs);
      if (movedExisting) await fs.rm(backup, { recursive: true, force: true }).catch(() => {
      });
      await removeRuntimeRecovery(transaction, fs);
    } catch (error) {
      if (installed) {
        throw preserveRuntimeLock(error);
      }
      if (movedExisting) {
        try {
          await fs.rename(backup, lockedTarget);
          movedExisting = false;
        } catch (restoreError) {
          const failedRecovery = { ...transaction.recovery, phase: "restore-failed" };
          try {
            await updateRuntimeRecovery(transaction, "restore-failed", fs);
          } catch {
          }
          const failure = new GatedLoopError(
            "ATOMIC_RESTORE_FAILED",
            "Unable to restore the previous directory after a failed replacement",
            {
              details: {
                installCode: error.code,
                restoreCode: restoreError.code,
                backup,
                recovery: await recoveryDetails(
                  transaction.descriptor,
                  fs,
                  transaction.record,
                  transaction.record,
                  transaction.recovery ?? failedRecovery
                )
              }
            }
          );
          throw preserveRuntimeLock(failure);
        }
      }
      if (!installed) {
        await fs.rm(staging, { recursive: true, force: true }).catch(() => {
        });
      }
      if (recoveryStarted) {
        try {
          await removeRuntimeRecovery(transaction, fs);
        } catch (cleanupError) {
          throw cleanupError;
        }
      }
      if (error && (typeof error === "object" || typeof error === "function")) {
        delete error[PRESERVE_RUNTIME_LOCK];
      }
      throw error;
    }
  };
  const active = activeRuntimeTransaction(descriptor);
  if (active) return replace(active);
  return withRuntimeDirectoryTransaction(
    descriptor.target,
    ({ record, ...ownedDescriptor }) => replace({
      descriptor: ownedDescriptor,
      record
    }),
    { fs, pathApi, platform }
  );
}

// src/baseline/test-command.mjs
var CONTROL = /[\u0000-\u001F\u007F-\u009F]/;
var SHELL_EXECUTABLES = /* @__PURE__ */ new Set([
  "ash",
  "bash",
  "csh",
  "dash",
  "elvish",
  "fish",
  "hush",
  "ksh",
  "ksh93",
  "mksh",
  "nu",
  "nushell",
  "osh",
  "pdksh",
  "sh",
  "tcsh",
  "xonsh",
  "ysh",
  "zsh",
  "cmd",
  "command.com",
  "powershell",
  "pwsh"
]);
var STRING_INTERPRETER_FLAGS = /* @__PURE__ */ new Map([
  ["bun", /* @__PURE__ */ new Set(["-e", "--eval", "-p", "--print"])],
  ["deno", /* @__PURE__ */ new Set(["-e", "--eval"])],
  ["lua", /* @__PURE__ */ new Set(["-e"])],
  ["node", /* @__PURE__ */ new Set(["-e", "--eval", "-p", "--print"])],
  ["perl", /* @__PURE__ */ new Set(["-e"])],
  ["php", /* @__PURE__ */ new Set([
    "-r",
    "--run",
    "-B",
    "--process-begin",
    "-R",
    "--process-code",
    "-E",
    "--process-end"
  ])],
  ["py", /* @__PURE__ */ new Set(["-c", "--command"])],
  ["python", /* @__PURE__ */ new Set(["-c", "--command"])],
  ["pypy", /* @__PURE__ */ new Set(["-c", "--command"])],
  ["ruby", /* @__PURE__ */ new Set(["-e", "--eval"])]
]);
var STRING_INTERPRETER_SUBCOMMANDS = /* @__PURE__ */ new Map([
  ["deno", /* @__PURE__ */ new Set(["eval"])]
]);
var INTERPRETER_OPTIONS_WITH_VALUES = /* @__PURE__ */ new Map([
  ["deno", /* @__PURE__ */ new Set(["--config", "--import-map", "--node-modules-dir"])],
  ["lua", /* @__PURE__ */ new Set(["-l"])],
  ["node", /* @__PURE__ */ new Set([
    "-r",
    "--require",
    "--import",
    "--loader",
    "--experimental-loader",
    "--conditions",
    "--input-type",
    "--redirect-warnings",
    "--env-file",
    "--env-file-if-exists",
    "--icu-data-dir",
    "--openssl-config",
    "--snapshot-blob",
    "--inspect-port",
    "--diagnostic-dir",
    "--report-dir",
    "--report-directory",
    "--report-filename",
    "--test-concurrency",
    "--test-name-pattern",
    "--test-reporter",
    "--test-reporter-destination",
    "--test-shard",
    "--test-timeout",
    "--title",
    "--experimental-default-type",
    "--dns-result-order",
    "--unhandled-rejections",
    "--disable-proto",
    "--trace-event-categories"
  ])],
  ["perl", /* @__PURE__ */ new Set(["-I"])],
  ["php", /* @__PURE__ */ new Set(["-c", "-d", "-z"])],
  ["python", /* @__PURE__ */ new Set(["-W", "-X", "-Q", "--check-hash-based-pycs"])],
  ["pypy", /* @__PURE__ */ new Set(["-W", "-X", "-Q"])],
  ["ruby", /* @__PURE__ */ new Set(["-I", "-r", "-C", "-E", "--encoding", "--external-encoding", "--internal-encoding"])]
]);
var INTERPRETER_ENTRYPOINT_OPTIONS = /* @__PURE__ */ new Map([
  ["node", /* @__PURE__ */ new Set(["--run"])],
  ["php", /* @__PURE__ */ new Set(["-f"])],
  ["python", /* @__PURE__ */ new Set(["-m"])],
  ["pypy", /* @__PURE__ */ new Set(["-m"])],
  ["ruby", /* @__PURE__ */ new Set(["-S"])]
]);
var INTERPRETER_BOOLEAN_OPTIONS = /* @__PURE__ */ new Map([
  ["node", /* @__PURE__ */ new Set(["-c", "--check", "--no-warnings", "--test"])]
]);
var EXEC_OPTIONS_WITH_VALUES = /* @__PURE__ */ new Set([
  "-p",
  "--package",
  "--cache",
  "--userconfig",
  "--registry",
  "--prefix",
  "-w",
  "--workspace",
  "--loglevel"
]);
var EXEC_BOOLEAN_OPTIONS = /* @__PURE__ */ new Set([
  "-y",
  "--yes",
  "--no",
  "-q",
  "--quiet",
  "-s",
  "--silent",
  "--workspaces",
  "--include-workspace-root",
  "--ignore-existing"
]);
var NPM_OPTIONS_WITH_VALUES = /* @__PURE__ */ new Set([
  "-C",
  "--prefix",
  "--cache",
  "--userconfig",
  "--registry",
  "-w",
  "--workspace",
  "--loglevel"
]);
var NPM_BOOLEAN_OPTIONS = /* @__PURE__ */ new Set([
  "-q",
  "--quiet",
  "-s",
  "--silent",
  "--verbose",
  "--workspaces",
  "--include-workspace-root",
  "--no-progress",
  "--color",
  "--no-color"
]);
function executableName(value) {
  return value.toLowerCase().replaceAll("\\", "/").split("/").at(-1).replace(/\.(?:exe|cmd|bat)$/, "");
}
function isShellExecutable(value) {
  return SHELL_EXECUTABLES.has(executableName(value));
}
function interpreterKind(value) {
  const name = executableName(value);
  if (/^pyw?$/.test(name)) return "python";
  if (/^(?:pythonw?)\d*(?:\.\d+)*$/.test(name)) return "python";
  if (/^pypy\d*(?:\.\d+)*$/.test(name)) return "pypy";
  if (/^node(?:js)?\d*(?:\.\d+)*$/.test(name)) return "node";
  if (/^rubyw\d*(?:\.\d+)*$/.test(name)) return "ruby";
  if (/^wperl\d*(?:\.\d+)*$/.test(name)) return "perl";
  if (/^(?:bun|deno)\d*(?:\.\d+)*$/.test(name)) return name.startsWith("bun") ? "bun" : "deno";
  for (const kind of ["ruby", "perl", "php", "lua"]) {
    if (new RegExp(`^${kind}\\d*(?:\\.\\d+)*$`).test(name)) return kind;
  }
  return STRING_INTERPRETER_FLAGS.has(name) ? name : void 0;
}
function isStringFlag(argument, kind, flags) {
  if (kind === "python" || kind === "pypy") {
    if (!/^-[^-]/.test(argument)) return [...flags].some((flag) => argument === flag || flag.startsWith("--") && argument.startsWith(`${flag}=`));
    for (const option of argument.slice(1)) {
      if (option === "c") return true;
      if (["W", "X", "Q"].includes(option)) return false;
    }
    return false;
  }
  if (kind === "perl" && /^-[^-]/.test(argument)) {
    for (const option of argument.slice(1)) {
      if (option === "e" || option === "E") return true;
      if (["I", "M", "m", "F", "C", "D", "U"].includes(option)) return false;
    }
  }
  if (kind === "ruby" && /^-[a-z]*e/.test(argument)) return true;
  return [...flags].some((flag) => argument === flag || flag.startsWith("--") && argument.startsWith(`${flag}=`) || flag.length === 2 && argument.startsWith(flag) && argument.length > flag.length);
}
function invokesInterpreterString(argv, executableIndex, kind, flags) {
  const subcommands = STRING_INTERPRETER_SUBCOMMANDS.get(kind) ?? /* @__PURE__ */ new Set();
  const valueOptions = INTERPRETER_OPTIONS_WITH_VALUES.get(kind) ?? /* @__PURE__ */ new Set();
  const entrypointOptions = INTERPRETER_ENTRYPOINT_OPTIONS.get(kind) ?? /* @__PURE__ */ new Set();
  const booleanOptions = INTERPRETER_BOOLEAN_OPTIONS.get(kind) ?? /* @__PURE__ */ new Set();
  let unknownOptionMayTakeValue = false;
  for (let index = executableIndex + 1; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "--") return false;
    if (isStringFlag(argument, kind, flags)) return true;
    if (subcommands.has(argument.toLowerCase())) return true;
    if ([...entrypointOptions].some((option) => argument === option || argument.startsWith(`${option}=`) || option.length === 2 && argument.startsWith(option) && argument.length > 2)) return false;
    const valueOption = [...valueOptions].find((option) => argument === option || argument.startsWith(`${option}=`) || option.length === 2 && argument.startsWith(option) && argument.length > 2);
    if (valueOption) {
      if (argument === valueOption) index++;
      unknownOptionMayTakeValue = false;
      continue;
    }
    if (booleanOptions.has(argument)) {
      unknownOptionMayTakeValue = false;
      continue;
    }
    if (argument.startsWith("-")) {
      unknownOptionMayTakeValue = kind === "node" && !argument.includes("=");
      continue;
    }
    if (unknownOptionMayTakeValue) {
      const laterCreatesString = argv.slice(index + 1).some((later) => isStringFlag(later, kind, flags) || subcommands.has(later.toLowerCase()));
      if (laterCreatesString) {
        unknownOptionMayTakeValue = false;
        continue;
      }
    }
    return false;
  }
  return false;
}
function optionMatch(argument, options) {
  return [...options].find((value) => argument === value || value.startsWith("--") && argument.startsWith(`${value}=`) || value.length === 2 && argument.startsWith(value) && argument.length > 2);
}
function envCommand(argv, start) {
  const optionsWithValues = /* @__PURE__ */ new Set(["-u", "--unset", "-C", "--chdir", "-a", "--argv0"]);
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "-S" || argument.startsWith("-S") || argument === "--split-string" || argument.startsWith("--split-string=")) return { rejected: true };
    if (argument === "--") return { index: index + 1 };
    const option = optionMatch(argument, optionsWithValues);
    if (option) {
      if (argument === option) index++;
      continue;
    }
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(argument) || argument.startsWith("-")) continue;
    return { index };
  }
  return {};
}
function multiCallApplet(argv, start) {
  const argument = argv[start];
  if (argument === "--") return start + 1;
  if (argument?.startsWith("-")) return void 0;
  return argument === void 0 ? void 0 : start;
}
function wrapperCommand(argv, start, optionsWithValues = /* @__PURE__ */ new Set()) {
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "--") return index + 1;
    const option = optionMatch(argument, optionsWithValues);
    if (option) {
      if (argument === option) index++;
      continue;
    }
    if (argument.startsWith("-")) continue;
    return index;
  }
  return void 0;
}
function execCommand(argv, start) {
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "--") return { index: index + 1 };
    if (argument === "-c" || argument.startsWith("-c") && !argument.startsWith("--") || argument === "--call" || argument.startsWith("--call=")) return { rejected: true };
    const valueOption = optionMatch(argument, EXEC_OPTIONS_WITH_VALUES);
    if (valueOption) {
      if (argument === valueOption) {
        if (index + 1 >= argv.length) return { rejected: true };
        index++;
      }
      continue;
    }
    if (EXEC_BOOLEAN_OPTIONS.has(argument) || [...EXEC_BOOLEAN_OPTIONS].some((option) => option.startsWith("--") && argument.startsWith(`${option}=`))) continue;
    if (argument.startsWith("-")) return { rejected: true };
    return { index };
  }
  return {};
}
function npmCommand(argv, start) {
  for (let index = start; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "--") return {};
    const valueOption = optionMatch(argument, NPM_OPTIONS_WITH_VALUES);
    if (valueOption) {
      if (argument === valueOption) {
        if (index + 1 >= argv.length) return { rejected: true };
        index++;
      }
      continue;
    }
    if (NPM_BOOLEAN_OPTIONS.has(argument) || [...NPM_BOOLEAN_OPTIONS].some((option) => option.startsWith("--") && argument.startsWith(`${option}=`))) continue;
    if (argument.startsWith("-")) return { rejected: true };
    return ["exec", "exe", "x"].includes(argument.toLowerCase()) ? execCommand(argv, index + 1) : {};
  }
  return {};
}
function inspectExecutableChain(argv, executableIndex = 0, depth = 0) {
  if (executableIndex >= argv.length || depth > 8) return false;
  const name = executableName(argv[executableIndex]);
  if (isShellExecutable(name)) return true;
  const kind = interpreterKind(name);
  const flags = kind && STRING_INTERPRETER_FLAGS.get(kind);
  if (flags) return invokesInterpreterString(argv, executableIndex, kind, flags);
  if (name === "env") {
    const command = envCommand(argv, executableIndex + 1);
    return command.rejected === true || command.index !== void 0 && inspectExecutableChain(argv, command.index, depth + 1);
  }
  if (name === "busybox" || name === "toybox") {
    const command = multiCallApplet(argv, executableIndex + 1);
    return command !== void 0 && inspectExecutableChain(argv, command, depth + 1);
  }
  if (name === "wsl") {
    const command = wrapperCommand(
      argv,
      executableIndex + 1,
      /* @__PURE__ */ new Set(["-d", "--distribution", "-u", "--user", "--cd", "--shell-type"])
    );
    return command === void 0 || inspectExecutableChain(argv, command, depth + 1);
  }
  if (name === "npx") {
    const command = execCommand(argv, executableIndex + 1);
    return command.rejected === true || command.index !== void 0 && inspectExecutableChain(argv, command.index, depth + 1);
  }
  if (name === "npm") {
    const command = npmCommand(argv, executableIndex + 1);
    return command.rejected === true || command.index !== void 0 && inspectExecutableChain(argv, command.index, depth + 1);
  }
  return false;
}
function normalizeTestArgv(value) {
  if (!Array.isArray(value) || value.length === 0 || typeof value[0] !== "string" || value[0].trim().length === 0 || /\s/.test(value[0]) || value.some((entry) => typeof entry !== "string" || entry.length === 0 || CONTROL.test(entry)) || inspectExecutableChain(value)) return null;
  return [...value];
}

// src/light/build-brief.mjs
var RISK_RULES = Object.freeze([
  ["loadBearing", false, "- No load-bearing file changes."],
  ["breaking", false, "- No breaking changes."],
  ["migrations", false, "- No migrations (schema, data, config, storage, API-version, or dependency)."],
  ["dependencyChange", false, "- No dependency changes."],
  ["newDependency", false, "- No new dependencies."],
  ["externalContract", false, "- No external contract changes."],
  ["permissions", false, "- No permission changes."],
  ["authentication", false, "- No authentication changes."],
  ["stateMachine", false, "- No state-machine changes."],
  ["transaction", false, "- No transaction changes."],
  ["concurrency", false, "- No concurrency changes."],
  ["idempotency", false, "- No idempotency changes."],
  ["unresolvedOptions", 0, "- No unresolved multi-option decisions."],
  ["thresholdDecision", false, "- No threshold decisions."],
  ["fileCountExceeded", false, "- No more than three files will be changed."],
  ["impactKnown", true, "- Impact is known."]
]);
var FULL_RISK_CONFIRMATIONS = Object.freeze(RISK_RULES.map(([, , text2]) => text2));
function fail(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}
function containsMarkdownHeading(text2) {
  return text2.split("\n").some((line) => {
    let content2 = line;
    let previous;
    do {
      previous = content2;
      content2 = content2.replace(/^\s{0,3}>\s?/, "").replace(/^\s{0,3}(?:[-+*]|\d+[.)])\s+/, "");
    } while (content2 !== previous);
    return /^\s{0,3}#{1,6}\s/.test(content2) || /^\s{0,3}(?:=+|-+)\s*$/.test(content2);
  });
}
function validateText(value, field) {
  if (typeof value !== "string" || value.trim().length === 0) fail("LIGHT_BRIEF_INVALID", `${field} must be nonempty`, { field });
  const text2 = value.replace(/\r\n?/g, "\n").trim();
  if (/[\u0000-\u0009\u000B\u000C\u000E-\u001F\u007F-\u009F]/.test(text2)) {
    fail("LIGHT_BRIEF_INVALID", `${field} contains control characters`, { field });
  }
  if (/\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?/i.test(text2)) {
    fail("LIGHT_BRIEF_PLACEHOLDER", `${field} contains placeholder content`, { field });
  }
  if (containsMarkdownHeading(text2)) {
    fail("LIGHT_BRIEF_INVALID", `${field} cannot contain headings`, { field });
  }
  return text2;
}
function normalizeList(value, field) {
  const values = Array.isArray(value) ? value : [value];
  if (values.length === 0) fail("LIGHT_BRIEF_INVALID", `${field} must be nonempty`, { field });
  return values.map((entry, index) => {
    const text2 = validateText(entry, `${field}[${index}]`);
    if (/[\u0000-\u001F\u007F]/.test(text2)) fail("LIGHT_BRIEF_INVALID", `${field} entries must be single-line`, { field });
    return text2;
  });
}
function normalizeScope(value) {
  const entries = normalizeList(value, "scope");
  let normalized;
  try {
    normalized = normalizeSignals({ modifiesFiles: entries, writesFiles: true, impactKnown: true });
  } catch {
    fail("LIGHT_BRIEF_INVALID", "Scope must contain safe relative file paths", { field: "scope" });
  }
  if (normalized.loadBearing || normalized.modifiesFiles.length > 3) {
    fail("LIGHT_BRIEF_FULL_RISK", "Scope requires Full mode", { field: "scope" });
  }
  return normalized.modifiesFiles;
}
function normalizeAcceptance(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("LIGHT_BRIEF_INVALID", "Acceptance must be structured");
  const outcomes = normalizeList(value.outcomes ?? value.observableOutcomes ?? [], "acceptance.outcomes");
  if (!Array.isArray(value.testCommands) || value.testCommands.length === 0) {
    fail("LIGHT_BRIEF_INVALID", "Acceptance requires at least one JSON argv test command");
  }
  const testCommands2 = value.testCommands.map((command, index) => {
    const argv = normalizeTestArgv(command);
    if (!argv) fail("LIGHT_BRIEF_INVALID", "Acceptance test command must be a safe nonempty argv array", { index });
    return argv;
  });
  return { outcomes, testCommands: testCommands2 };
}
function validateRisks(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("LIGHT_BRIEF_INVALID", "Risks must be structured");
  for (const [field, safeValue] of RISK_RULES) {
    if (!Object.hasOwn(value, field)) {
      fail("LIGHT_BRIEF_RISK_CONFIRMATION_REQUIRED", `Risks must explicitly confirm ${field}`, { field });
    }
    if (value[field] !== safeValue) fail("LIGHT_BRIEF_FULL_RISK", `${field} requires Full mode`, { field });
  }
}
function validateLightBrief(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) fail("LIGHT_BRIEF_INVALID", "Light brief must be a mapping");
  const goal = validateText(input.goal, "goal");
  const scope = normalizeScope(input.scope ?? []);
  const acceptance = normalizeAcceptance(input.acceptance);
  validateRisks(input.risks);
  return { goal, scope, acceptance, risks: Object.fromEntries(RISK_RULES.map(([field]) => [field, input.risks[field]])) };
}
function buildLightBrief(input) {
  const { goal, scope, acceptance } = validateLightBrief(input);
  return [
    "## Goal",
    goal,
    "",
    "## Scope",
    ...scope.map((entry) => `- ${entry}`),
    "",
    "## Acceptance",
    ...acceptance.outcomes.map((entry) => `- ${entry}`),
    ...acceptance.testCommands.map((argv) => `- Test command: ${JSON.stringify(argv)}`),
    "",
    "## Risks",
    ...FULL_RISK_CONFIRMATIONS,
    ""
  ].join("\n");
}

// src/light/freeze.mjs
import * as fsPromises2 from "node:fs/promises";
import path5 from "node:path";

// src/mode/host-runtime.mjs
var AGENT_RUNTIME_PATTERN = /^[a-z][a-z0-9._-]{0,63}$/;
function isAgentRuntime(value) {
  return typeof value === "string" && AGENT_RUNTIME_PATTERN.test(value);
}
function normalizeHostRuntime(hostRuntime) {
  if (hostRuntime === void 0 || hostRuntime === null) return void 0;
  if (!isAgentRuntime(hostRuntime)) {
    throw new GatedLoopError("HOST_RUNTIME_INVALID", "hostRuntime must be a safe lowercase Agent identifier");
  }
  return hostRuntime;
}
function requireHostRuntime(hostRuntime) {
  const normalized = normalizeHostRuntime(hostRuntime);
  if (!normalized) {
    throw new GatedLoopError("HOST_RUNTIME_REQUIRED", "A writing workflow requires a --host-runtime Agent identifier");
  }
  return normalized;
}

// src/handoff/render.mjs
function links(values) {
  return Array.isArray(values) ? values.join(",") : "";
}
function renderDevelopmentHandoff({
  task,
  reviewer,
  authorityFile,
  scope = [],
  tasks,
  acceptance,
  testCommands: testCommands2
} = {}) {
  const scopeLines = Array.isArray(scope) ? scope.map((entry) => `- ${entry}`) : typeof scope === "string" && scope.length > 0 ? [scope] : [];
  const lines = [
    "# Development Handoff",
    "",
    `Task: ${task}`,
    `Reviewed by: ${reviewer}`,
    `Frozen authority: \`${authorityFile}\``,
    "",
    `The frozen \`${authorityFile}\` is the only development authority.`,
    "",
    "## Development Rules",
    "- Implement only the listed tasks within the frozen Scope.",
    "- Do not reanalyze, reinterpret, clarify, or rewrite requirements.",
    "- Do not change acceptance criteria or any frozen artifact.",
    "- If the frozen authority is incomplete or contradictory, return `BLOCKED`; do not resolve it by analysis.",
    "- Do not judge or report `PASS`.",
    ""
  ];
  if (scopeLines.length > 0) {
    lines.push("## Scope", ...scopeLines, "");
  }
  lines.push(
    "## Tasks",
    ...tasks.map((entry) => `- ${entry.id} [${links(entry.requirementIds)}] [${links(entry.acceptanceIds)}] ${entry.text}`),
    "",
    "## Acceptance",
    ...acceptance.map((entry) => `- ${entry.id} [${links(entry.requirementIds)}] ${entry.expectedResult.replaceAll("\n", " ")}`),
    "",
    "## Test Commands",
    ...testCommands2.map((argv) => `- ${JSON.stringify(argv)}`),
    ""
  );
  return lines.join("\n");
}

// src/handoff/files.mjs
var DEVELOPMENT_HANDOFF_FILE = "development-handoff.md";
var LEGACY_DEVELOPMENT_HANDOFF_FILE = "handoff-to-claude.md";
function handoffFileFromNames(names) {
  const matches = [DEVELOPMENT_HANDOFF_FILE, LEGACY_DEVELOPMENT_HANDOFF_FILE].filter((name) => names.includes(name));
  return matches.length === 1 ? matches[0] : null;
}

// src/light/artifacts.mjs
var SCHEMA_VERSION = 1;
var GENERATOR_VERSION = 1;
function json(value) {
  return `${JSON.stringify(value, null, 2)}
`;
}
function numbered(prefix, index) {
  return `${prefix}-${String(index + 1).padStart(3, "0")}`;
}
function buildLightArtifacts({ task, reviewer, brief, markdown } = {}) {
  const baselineFingerprint = sha256Bytes(Buffer.from(markdown, "utf8"));
  const acceptanceEntries = brief.acceptance.outcomes.map((expectedResult, index) => ({
    id: numbered("A", index),
    requirementIds: ["R-001"],
    expectedResult,
    trace: { file: "light-brief.md", section: "Acceptance", index: index + 1 }
  }));
  const taskEntries = [{
    id: "T-001",
    requirementIds: ["R-001"],
    acceptanceIds: acceptanceEntries.map(({ id }) => id),
    text: brief.goal,
    scope: [...brief.scope],
    trace: { file: "light-brief.md", section: "Goal" }
  }];
  const acceptance = {
    schemaVersion: SCHEMA_VERSION,
    generatorVersion: GENERATOR_VERSION,
    baselineFingerprint,
    acceptance: acceptanceEntries
  };
  const tasks = {
    schemaVersion: SCHEMA_VERSION,
    generatorVersion: GENERATOR_VERSION,
    baselineFingerprint,
    tasks: taskEntries
  };
  const decisionLog = [
    "# Decision Log",
    "",
    `- Host review: ${reviewer}.`,
    "- User confirmation: confirmed.",
    "- No additional decisions recorded.",
    ""
  ].join("\n");
  const handoff = renderDevelopmentHandoff({
    task,
    reviewer,
    authorityFile: "light-brief.md",
    scope: brief.scope,
    tasks: taskEntries,
    acceptance: acceptanceEntries,
    testCommands: brief.acceptance.testCommands
  });
  return {
    acceptance,
    tasks,
    files: {
      "light-brief.md": markdown,
      "acceptance.json": json(acceptance),
      "tasks.json": json(tasks),
      "decision-log.md": decisionLog,
      [DEVELOPMENT_HANDOFF_FILE]: handoff
    }
  };
}

// src/core/runtime-layout.mjs
import path4 from "node:path";
var MUTABLE_RUNTIME_ENTRIES = Object.freeze([
  "development-overview.md",
  "progress.md",
  "final-acceptance-report.md",
  "rounds"
]);
async function validateMutableRuntimeEntries(target, names, { fs } = {}) {
  const mutable = names.filter((name) => MUTABLE_RUNTIME_ENTRIES.includes(name));
  for (const name of mutable) {
    const stat = await fs.lstat(path4.join(target, name));
    const valid = name === "rounds" ? stat.isDirectory() : stat.isFile();
    if (!valid || stat.isSymbolicLink()) {
      throw new GatedLoopError("RUNTIME_ARTIFACT_INVALID", `Runtime artifact has an invalid type: ${name}`);
    }
  }
  return names.filter((name) => !MUTABLE_RUNTIME_ENTRIES.includes(name));
}

// src/light/freeze.mjs
var STATE_SCHEMA_VERSION = 1;
var ARTIFACT_NAMES = Object.freeze([
  "acceptance.json",
  "decision-log.md",
  DEVELOPMENT_HANDOFF_FILE,
  "light-brief.md",
  "mode.json",
  "source-manifest.json",
  "state.json",
  "tasks.json"
]);
var HASHED_ARTIFACT_NAMES = Object.freeze(ARTIFACT_NAMES.filter((name) => name !== "state.json"));
function json2(value) {
  return `${JSON.stringify(value, null, 2)}
`;
}
function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
function fingerprintInput(classification, markdown, hostRuntime, task) {
  return sha256Bytes(Buffer.from(canonicalJson({
    classification: {
      mode: classification.mode,
      reasons: classification.reasons,
      confidence: classification.confidence,
      evaluatedInputs: classification.evaluatedInputs
    },
    hostRuntime,
    lightBrief: markdown,
    state: { schemaVersion: STATE_SCHEMA_VERSION, reviewer: hostRuntime },
    task
  }), "utf8"));
}
function artifactHashes(files, names = HASHED_ARTIFACT_NAMES) {
  return Object.fromEntries(names.map((name) => {
    const value = Buffer.isBuffer(files[name]) ? files[name] : Buffer.from(files[name], "utf8");
    return [name, sha256Bytes(value)];
  }));
}
function frozenFingerprint(state) {
  const { frozenFingerprint: ignored, ...metadata } = state;
  return sha256Bytes(Buffer.from(canonicalJson(metadata), "utf8"));
}
function hasExactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) && canonicalJson(Object.keys(value).sort()) === canonicalJson([...keys].sort());
}
function isCanonicalTimestamp(value) {
  if (typeof value !== "string") return false;
  const date = new Date(value);
  return !Number.isNaN(date.valueOf()) && date.toISOString() === value;
}
function validateTask(task) {
  const reserved = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/;
  if (typeof task !== "string" || !/^[a-z0-9][a-z0-9._-]*$/.test(task) || task.endsWith(".") || reserved.test(task)) {
    throw new GatedLoopError("LIGHT_TASK_INVALID", "Light task must be a safe single path segment");
  }
}
function validateClassification(classification) {
  const valid = classification && typeof classification === "object" && !Array.isArray(classification) && classification.mode === "light" && Array.isArray(classification.reasons) && ["high", "medium"].includes(classification.confidence) && classification.evaluatedInputs && typeof classification.evaluatedInputs === "object";
  if (!valid) throw new GatedLoopError("LIGHT_MODE_REQUIRED", "Only a Light classification can be frozen");
  let replay;
  try {
    replay = classifyMode({ ...classification.evaluatedInputs, requestedMode: null });
  } catch {
    throw new GatedLoopError("LIGHT_MODE_REQUIRED", "Light classification inputs are invalid");
  }
  const expectedInputs = { ...replay.evaluatedInputs, requestedMode: classification.evaluatedInputs.requestedMode ?? null };
  const consistent = replay.mode === "light" && canonicalJson(replay.reasons) === canonicalJson(classification.reasons) && replay.confidence === classification.confidence && canonicalJson(expectedInputs) === canonicalJson(classification.evaluatedInputs);
  if (!consistent) throw new GatedLoopError("LIGHT_MODE_REQUIRED", "Light classification does not match its evaluated inputs");
  return classification;
}
function timestamp(now) {
  const value = typeof now === "function" ? now() : /* @__PURE__ */ new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) throw new GatedLoopError("LIGHT_TIMESTAMP_INVALID", "Freeze timestamp is invalid");
  return date.toISOString();
}
function parseJson(bytes) {
  return JSON.parse(bytes.toString("utf8"));
}
async function readExisting(target, fs, expectedTask) {
  let stat;
  try {
    stat = await fs.lstat(target);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new GatedLoopError("LIGHT_SOURCE_CHANGED", "Existing Light artifact path is not a directory");
  }
  try {
    const allNames = (await fs.readdir(target)).sort();
    const names = (await validateMutableRuntimeEntries(target, allNames, { fs })).sort();
    const handoffName = handoffFileFromNames(names);
    const artifactNames = ARTIFACT_NAMES.map((name) => name === DEVELOPMENT_HANDOFF_FILE ? handoffName : name).sort();
    if (!handoffName || canonicalJson(names) !== canonicalJson(artifactNames)) throw new Error("unexpected frozen artifact");
    const bytes = Object.fromEntries(await Promise.all(names.map(async (name) => [
      name,
      await readSafeRegularFile(target, name, { fs })
    ])));
    const mode = parseJson(bytes["mode.json"]);
    const manifest = parseJson(bytes["source-manifest.json"]);
    const state = parseJson(bytes["state.json"]);
    const modeKeys = ["classifierVersion", "mode", "reasons", "confidence", "evaluatedInputs", "hostRuntime", "createdAt"];
    const stateKeys = [
      "schemaVersion",
      "task",
      "mode",
      "stage",
      "hostRuntime",
      "reviewer",
      "sourceFingerprint",
      "inputFingerprint",
      "artifactHashes",
      "updatedAt",
      "frozenFingerprint"
    ];
    if (!hasExactKeys(mode, modeKeys) || !hasExactKeys(manifest, ["version", "files", "fingerprint", "inputFingerprint"]) || !hasExactKeys(state, stateKeys) || !isCanonicalTimestamp(mode.createdAt)) throw new Error("invalid frozen schema");
    validateClassification({
      mode: mode.mode,
      reasons: mode.reasons,
      confidence: mode.confidence,
      evaluatedInputs: mode.evaluatedInputs
    });
    const hostRuntime = normalizeHostRuntime(mode.hostRuntime);
    const sourceFiles = [
      { path: "light-brief.md", sha256: sha256Bytes(bytes["light-brief.md"]) },
      { path: "mode.json", sha256: sha256Bytes(bytes["mode.json"]) }
    ];
    const validSourceFiles = Array.isArray(manifest.files) && manifest.files.length === sourceFiles.length && sourceFiles.every((entry, index) => hasExactKeys(manifest.files[index], ["path", "sha256"]) && manifest.files[index].path === entry.path && manifest.files[index].sha256 === entry.sha256);
    const hashNames = artifactNames.filter((name) => name !== "state.json");
    const hashes = artifactHashes(bytes, hashNames);
    const valid = manifest.version === 1 && validSourceFiles && manifest.fingerprint === manifestFingerprint(sourceFiles) && typeof manifest.inputFingerprint === "string" && /^[a-f0-9]{64}$/.test(manifest.inputFingerprint) && mode.classifierVersion === CLASSIFIER_VERSION && mode.mode === "light" && state.schemaVersion === STATE_SCHEMA_VERSION && state.task === expectedTask && state.mode === "light" && state.stage === "BASELINE_FROZEN" && state.hostRuntime === hostRuntime && state.reviewer === hostRuntime && state.sourceFingerprint === manifest.fingerprint && state.inputFingerprint === manifest.inputFingerprint && state.updatedAt === mode.createdAt && isCanonicalTimestamp(state.updatedAt) && canonicalJson(state.artifactHashes) === canonicalJson(hashes) && state.frozenFingerprint === frozenFingerprint(state) && manifest.inputFingerprint === fingerprintInput(
      mode,
      bytes["light-brief.md"].toString("utf8"),
      hostRuntime,
      expectedTask
    );
    if (!valid) throw new Error("invalid frozen artifact");
    return { mode, manifest, state, bytes, handoffName, artifactNames };
  } catch {
    throw new GatedLoopError("LIGHT_SOURCE_CHANGED", "Existing Light artifacts are incomplete or changed");
  }
}
function outcome(target, existing, created) {
  return {
    created,
    idempotent: !created,
    mode: "light",
    stage: "BASELINE_FROZEN",
    artifactDir: target,
    artifacts: existing.artifactNames.map((name) => path5.join(target, name)),
    sourceFingerprint: existing.manifest.fingerprint,
    inputFingerprint: existing.manifest.inputFingerprint,
    hostRuntime: existing.mode.hostRuntime,
    reviewer: existing.mode.hostRuntime
  };
}
function generatedArtifactsMatch(existing, generated) {
  return Object.entries(generated.files).every(([name, content2]) => existing.bytes[name === DEVELOPMENT_HANDOFF_FILE ? existing.handoffName : name].toString("utf8") === content2);
}
async function freezeLightTaskLocked({
  root,
  task,
  classification,
  brief,
  hostRuntime,
  now = () => /* @__PURE__ */ new Date(),
  beforeCommit,
  fs = fsPromises2
} = {}) {
  const markdown = buildLightBrief(brief);
  const generated = buildLightArtifacts({ task, reviewer: hostRuntime, brief, markdown });
  const target = await assertSafePath(root, path5.join(".ai-dev-loop", task), { fs });
  const inputFingerprint = fingerprintInput(classification, markdown, hostRuntime, task);
  const existing = await readExisting(target, fs, task);
  if (existing) {
    if (existing.manifest.inputFingerprint !== inputFingerprint || !generatedArtifactsMatch(existing, generated)) {
      throw new GatedLoopError("LIGHT_SOURCE_CHANGED", "Frozen Light source differs from the requested input");
    }
    return outcome(target, existing, false);
  }
  const createdAt = timestamp(now);
  const mode = {
    classifierVersion: CLASSIFIER_VERSION,
    mode: classification.mode,
    reasons: classification.reasons,
    confidence: classification.confidence,
    evaluatedInputs: classification.evaluatedInputs,
    hostRuntime,
    createdAt
  };
  const modeText = json2(mode);
  const sourceFiles = [
    { path: "light-brief.md", sha256: sha256Bytes(Buffer.from(markdown, "utf8")) },
    { path: "mode.json", sha256: sha256Bytes(Buffer.from(modeText, "utf8")) }
  ];
  const manifest = {
    version: 1,
    files: sourceFiles,
    fingerprint: manifestFingerprint(sourceFiles),
    inputFingerprint
  };
  const files = {
    ...generated.files,
    "mode.json": modeText,
    "source-manifest.json": json2(manifest)
  };
  const state = {
    schemaVersion: STATE_SCHEMA_VERSION,
    task,
    mode: "light",
    stage: "BASELINE_FROZEN",
    hostRuntime,
    reviewer: hostRuntime,
    sourceFingerprint: manifest.fingerprint,
    inputFingerprint,
    artifactHashes: artifactHashes(files),
    updatedAt: createdAt
  };
  state.frozenFingerprint = frozenFingerprint(state);
  files["state.json"] = json2(state);
  try {
    await atomicWriteDirectory(target, async (staging) => {
      for (const name of ARTIFACT_NAMES) await atomicWriteFile(path5.join(staging, name), files[name], { fs });
      if (beforeCommit) await beforeCommit(staging);
    }, { fs });
  } catch (error) {
    if (!["EEXIST", "ENOTEMPTY", "EPERM"].includes(error.code)) throw error;
    const raced = await readExisting(target, fs, task);
    if (raced?.manifest.inputFingerprint === inputFingerprint && generatedArtifactsMatch(raced, generated)) {
      return outcome(target, raced, false);
    }
    throw new GatedLoopError("LIGHT_SOURCE_CHANGED", "A different Light source was frozen concurrently");
  }
  return outcome(target, { manifest, mode, artifactNames: ARTIFACT_NAMES }, true);
}
async function freezeLightTask(options = {}) {
  const {
    root,
    task,
    classification,
    brief,
    hostRuntime: suppliedHostRuntime,
    confirmed = false,
    fs = fsPromises2
  } = options;
  if (confirmed !== true) throw new GatedLoopError("CONFIRMATION_REQUIRED", "Light brief freeze requires explicit confirmation");
  if (typeof root !== "string" || root.length === 0) throw new GatedLoopError("LIGHT_ROOT_INVALID", "Project root is required");
  validateTask(task);
  let stableClassification;
  let stableBrief;
  try {
    stableClassification = structuredClone(classification);
    stableBrief = structuredClone(brief);
  } catch {
    throw new GatedLoopError("LIGHT_MODE_REQUIRED", "Light freeze inputs must be structured data");
  }
  validateClassification(stableClassification);
  const hostRuntime = requireHostRuntime(suppliedHostRuntime);
  const normalizedBrief = validateLightBrief(stableBrief);
  if (canonicalJson(normalizedBrief.scope) !== canonicalJson(stableClassification.evaluatedInputs.modifiesFiles)) {
    throw new GatedLoopError("LIGHT_SCOPE_MISMATCH", "Light brief scope must match classified write paths");
  }
  let rootStat;
  try {
    rootStat = await fs.lstat(root);
  } catch (error) {
    if (error.code === "ENOENT") throw new GatedLoopError("LIGHT_ROOT_INVALID", "Project root must already exist");
    throw error;
  }
  if (!rootStat.isDirectory()) throw new GatedLoopError("LIGHT_ROOT_INVALID", "Project root must be a directory");
  const target = await assertSafePath(root, path5.join(".ai-dev-loop", task), { fs });
  return withRuntimeDirectoryTransaction(
    target,
    () => freezeLightTaskLocked({
      ...options,
      classification: stableClassification,
      brief: normalizedBrief,
      hostRuntime,
      fs
    }),
    { fs }
  );
}
async function readLightPackage({ root, task, fs = fsPromises2 } = {}) {
  if (typeof root !== "string" || root.length === 0) throw new GatedLoopError("LIGHT_ROOT_INVALID", "Project root is required");
  validateTask(task);
  const target = await assertSafePath(root, path5.join(".ai-dev-loop", task), { fs });
  const existing = await readExisting(target, fs, task);
  if (!existing) throw new GatedLoopError("LIGHT_MODE_REQUIRED", "A frozen Light package is required");
  return { ...existing, target, stage: existing.state.stage, hostRuntime: existing.mode.hostRuntime };
}

// src/mode/persist.mjs
import * as fsPromises3 from "node:fs/promises";
import path6 from "node:path";
function json3(value) {
  return `${JSON.stringify(value, null, 2)}
`;
}
function canonicalJson2(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson2).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson2(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
function hasExactKeys2(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) && canonicalJson2(Object.keys(value).sort()) === canonicalJson2([...keys].sort());
}
function isCanonicalTimestamp2(value) {
  if (typeof value !== "string") return false;
  const date = new Date(value);
  return !Number.isNaN(date.valueOf()) && date.toISOString() === value;
}
function validateTask2(task) {
  const reserved = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/;
  if (typeof task !== "string" || !/^[a-z0-9][a-z0-9._-]*$/.test(task) || task.endsWith(".") || reserved.test(task)) {
    throw new GatedLoopError("MODE_TASK_INVALID", "Mode task must be a safe single path segment");
  }
}
function replayClassification(classification) {
  const valid = classification && typeof classification === "object" && !Array.isArray(classification) && classification.mode === "full" && Array.isArray(classification.reasons) && ["high", "medium"].includes(classification.confidence) && classification.evaluatedInputs && typeof classification.evaluatedInputs === "object";
  if (!valid) throw new GatedLoopError("FULL_MODE_REQUIRED", "Only a Full classification can be persisted");
  let replay;
  try {
    replay = classifyMode(classification.evaluatedInputs);
  } catch {
    throw new GatedLoopError("FULL_MODE_REQUIRED", "Full classification inputs are invalid");
  }
  const consistent = replay.mode === "full" && canonicalJson2(replay.reasons) === canonicalJson2(classification.reasons) && replay.confidence === classification.confidence && canonicalJson2(replay.evaluatedInputs) === canonicalJson2(classification.evaluatedInputs);
  if (!consistent) throw new GatedLoopError("FULL_MODE_REQUIRED", "Full classification does not match its evaluated inputs");
  return replay;
}
function timestamp2(now) {
  const value = typeof now === "function" ? now() : /* @__PURE__ */ new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) throw new GatedLoopError("MODE_TIMESTAMP_INVALID", "Mode timestamp is invalid");
  return date.toISOString();
}
function semanticMode(classification, hostRuntime) {
  return {
    mode: classification.mode,
    reasons: classification.reasons,
    confidence: classification.confidence,
    evaluatedInputs: classification.evaluatedInputs,
    hostRuntime: hostRuntime ?? null
  };
}
async function readExisting2(target, fs) {
  let stat;
  try {
    stat = await fs.lstat(target);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  if (!stat.isDirectory()) throw new GatedLoopError("MODE_SOURCE_CHANGED", "Existing mode artifact path is not a directory");
  try {
    const modeBytes = await readSafeRegularFile(target, "mode.json", { fs });
    const mode = JSON.parse(modeBytes.toString("utf8"));
    const keys = ["classifierVersion", "mode", "reasons", "confidence", "evaluatedInputs", "hostRuntime", "createdAt"];
    if (!hasExactKeys2(mode, keys) || mode.classifierVersion !== CLASSIFIER_VERSION || !isCanonicalTimestamp2(mode.createdAt)) {
      throw new Error("invalid mode artifact");
    }
    const classification = replayClassification(mode);
    const hostRuntime = requireHostRuntime(mode.hostRuntime);
    return { mode, classification, hostRuntime };
  } catch {
    throw new GatedLoopError("MODE_SOURCE_CHANGED", "Existing mode artifact is incomplete or changed");
  }
}
function result2(target, existing, created) {
  const value = {
    created,
    idempotent: !created,
    mode: "full",
    artifactDir: target,
    artifacts: [path6.join(target, "mode.json")]
  };
  if (existing.hostRuntime) value.hostRuntime = existing.hostRuntime;
  return value;
}
async function persistFullModeLocked({
  root,
  task,
  classification,
  hostRuntime: suppliedHostRuntime,
  now = () => /* @__PURE__ */ new Date(),
  beforeCommit,
  fs = fsPromises3
} = {}) {
  if (typeof root !== "string" || root.length === 0) throw new GatedLoopError("MODE_ROOT_INVALID", "Project root is required");
  validateTask2(task);
  let stableClassification;
  try {
    stableClassification = structuredClone(classification);
  } catch {
    throw new GatedLoopError("FULL_MODE_REQUIRED", "Full mode input must be structured data");
  }
  stableClassification = replayClassification(stableClassification);
  const hostRuntime = requireHostRuntime(suppliedHostRuntime);
  let rootStat;
  try {
    rootStat = await fs.lstat(root);
  } catch (error) {
    if (error.code === "ENOENT") throw new GatedLoopError("MODE_ROOT_INVALID", "Project root must already exist");
    throw error;
  }
  if (!rootStat.isDirectory()) throw new GatedLoopError("MODE_ROOT_INVALID", "Project root must be a directory");
  const target = await assertSafePath(root, path6.join(".ai-dev-loop", task), { fs });
  const desiredSemantic = semanticMode(stableClassification, hostRuntime);
  const existing = await readExisting2(target, fs);
  if (existing) {
    if (canonicalJson2(semanticMode(existing.classification, existing.hostRuntime)) !== canonicalJson2(desiredSemantic)) {
      throw new GatedLoopError("MODE_SOURCE_CHANGED", "Persisted mode differs from the requested route");
    }
    return result2(target, existing, false);
  }
  const mode = {
    classifierVersion: CLASSIFIER_VERSION,
    mode: stableClassification.mode,
    reasons: stableClassification.reasons,
    confidence: stableClassification.confidence,
    evaluatedInputs: stableClassification.evaluatedInputs,
    hostRuntime,
    createdAt: timestamp2(now)
  };
  try {
    await atomicWriteDirectory(target, async (staging) => {
      await atomicWriteFile(path6.join(staging, "mode.json"), json3(mode), { fs });
      if (beforeCommit) await beforeCommit(staging);
    }, { fs });
  } catch (error) {
    if (!["EEXIST", "ENOTEMPTY", "EPERM"].includes(error.code)) throw error;
    const raced = await readExisting2(target, fs);
    if (raced && canonicalJson2(semanticMode(raced.classification, raced.hostRuntime)) === canonicalJson2(desiredSemantic)) {
      return result2(target, raced, false);
    }
    throw new GatedLoopError("MODE_SOURCE_CHANGED", "A different mode source was persisted concurrently");
  }
  return result2(target, { mode, hostRuntime }, true);
}
async function persistFullMode(options = {}) {
  const {
    root,
    task,
    classification,
    hostRuntime,
    fs = fsPromises3
  } = options;
  if (typeof root !== "string" || root.length === 0) {
    throw new GatedLoopError("MODE_ROOT_INVALID", "Project root is required");
  }
  validateTask2(task);
  let stableClassification;
  try {
    stableClassification = structuredClone(classification);
  } catch {
    throw new GatedLoopError("FULL_MODE_REQUIRED", "Full mode input must be structured data");
  }
  replayClassification(stableClassification);
  requireHostRuntime(hostRuntime);
  let rootStat;
  try {
    rootStat = await fs.lstat(root);
  } catch (error) {
    if (error.code === "ENOENT") throw new GatedLoopError("MODE_ROOT_INVALID", "Project root must already exist");
    throw error;
  }
  if (!rootStat.isDirectory()) throw new GatedLoopError("MODE_ROOT_INVALID", "Project root must be a directory");
  const target = await assertSafePath(root, path6.join(".ai-dev-loop", task), { fs });
  return withRuntimeDirectoryTransaction(
    target,
    () => persistFullModeLocked({ ...options, classification: stableClassification, fs }),
    { fs }
  );
}

// src/work-items/model.mjs
import path8 from "node:path";

// src/baseline/sources.mjs
import * as fsPromises4 from "node:fs/promises";
import path7 from "node:path";
import { TextDecoder } from "node:util";

// src/baseline/parse.mjs
var BASELINE_SCHEMA_VERSION = 1;
var BASELINE_GENERATOR_VERSION = 1;
var FULL_BASELINE_SECTIONS = Object.freeze([
  "Goal",
  "Background",
  "Scope",
  "Non-Goals",
  "Requirements",
  "Acceptance",
  "Tasks",
  "Risks",
  "Test Commands",
  "Decisions"
]);
var PLACEHOLDER = /\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?|\blorem\s+ipsum\b/i;
var CONTROL2 = /[\u0000-\u0009\u000B\u000C\u000E-\u001F\u007F-\u009F]/;
var ID_NUMBER = "(?:00[1-9]|0[1-9]\\d|[1-9]\\d{2})";
var REQUIREMENT = new RegExp(`^### (R-${ID_NUMBER}) (\\S(?:.*\\S)?)$`);
var ACCEPTANCE = new RegExp(`^### (A-${ID_NUMBER}) \\[([^\\]]+)\\]$`);
var TASK = new RegExp(`^- \\[ \\] (T-${ID_NUMBER}) \\[([^\\]]+)\\] \\[([^\\]]+)\\] (\\S(?:.*\\S)?)$`);
function fail2(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}
function cleanBlock(lines) {
  let start = 0;
  let end = lines.length;
  while (start < end && lines[start].text.trim() === "") start++;
  while (end > start && lines[end - 1].text.trim() === "") end--;
  return lines.slice(start, end);
}
function content(lines, section) {
  const cleaned = cleanBlock(lines);
  if (cleaned.length === 0 || cleaned.every((line) => line.text.trim() === "")) {
    fail2("BASELINE_VALUE_INVALID", `${section} must be nonempty`, { section });
  }
  const text2 = cleaned.map((line) => line.text).join("\n");
  if (PLACEHOLDER.test(text2)) fail2("BASELINE_PLACEHOLDER", `${section} contains placeholder content`, { section });
  if (CONTROL2.test(text2)) fail2("BASELINE_VALUE_INVALID", `${section} contains control characters`, { section });
  return { text: text2, lines: cleaned };
}
function parseLinks(value, prefix, line, field) {
  const values = value.split(",").map((entry) => entry.trim());
  const pattern = new RegExp(`^${prefix}-${ID_NUMBER}$`);
  if (values.length === 0 || values.some((entry) => !pattern.test(entry)) || new Set(values).size !== values.length) {
    fail2("BASELINE_TRACE_INVALID", `${field} contains malformed or duplicate links`, { line, field });
  }
  return values;
}
function recordTrace(file, line) {
  return { file, line };
}
function parseRequirements(lines, file, fencedLines) {
  const values = [];
  const ids = /* @__PURE__ */ new Set();
  let index = 0;
  while (index < lines.length) {
    while (index < lines.length && lines[index].text.trim() === "") index++;
    if (index >= lines.length) break;
    const header = lines[index];
    const match = REQUIREMENT.exec(header.text);
    if (!match) fail2("BASELINE_TRACE_INVALID", "Requirement headings must use R-NNN and a title", { line: header.line });
    if (ids.has(match[1])) fail2("BASELINE_TRACE_INVALID", `Duplicate requirement ID: ${match[1]}`, { line: header.line });
    if (PLACEHOLDER.test(match[2])) fail2("BASELINE_PLACEHOLDER", `${match[1]} title contains placeholder content`, { line: header.line });
    ids.add(match[1]);
    index++;
    const bodyStart = index;
    while (index < lines.length && (fencedLines.has(lines[index].line - 1) || !lines[index].text.startsWith("### "))) index++;
    const body = content(lines.slice(bodyStart, index), match[1]);
    values.push({ id: match[1], title: match[2], text: body.text, trace: recordTrace(file, header.line) });
  }
  if (values.length === 0) fail2("BASELINE_VALUE_INVALID", "Requirements must contain at least one entry");
  return values;
}
function parseAcceptance(lines, file, fencedLines) {
  const values = [];
  const ids = /* @__PURE__ */ new Set();
  let index = 0;
  while (index < lines.length) {
    while (index < lines.length && lines[index].text.trim() === "") index++;
    if (index >= lines.length) break;
    const header = lines[index];
    const match = ACCEPTANCE.exec(header.text);
    if (!match) fail2("BASELINE_TRACE_INVALID", "Acceptance headings must use A-NNN [R-NNN,...]", { line: header.line });
    if (ids.has(match[1])) fail2("BASELINE_TRACE_INVALID", `Duplicate acceptance ID: ${match[1]}`, { line: header.line });
    ids.add(match[1]);
    const requirementIds = parseLinks(match[2], "R", header.line, match[1]);
    index++;
    const bodyStart = index;
    while (index < lines.length && (fencedLines.has(lines[index].line - 1) || !lines[index].text.startsWith("### "))) index++;
    const body = content(lines.slice(bodyStart, index), match[1]);
    values.push({ id: match[1], requirementIds, expectedResult: body.text, trace: recordTrace(file, header.line) });
  }
  if (values.length === 0) fail2("BASELINE_VALUE_INVALID", "Acceptance must contain at least one entry");
  return values;
}
function parseTasks(lines, file) {
  const values = [];
  const ids = /* @__PURE__ */ new Set();
  for (const line of lines) {
    if (line.text.trim() === "") continue;
    const match = TASK.exec(line.text);
    if (!match) fail2("BASELINE_TRACE_INVALID", "Tasks must use unchecked T-NNN traceable checklist entries", { line: line.line });
    if (ids.has(match[1])) fail2("BASELINE_TRACE_INVALID", `Duplicate task ID: ${match[1]}`, { line: line.line });
    ids.add(match[1]);
    if (PLACEHOLDER.test(match[4])) fail2("BASELINE_PLACEHOLDER", `${match[1]} contains placeholder content`, { line: line.line });
    values.push({
      id: match[1],
      requirementIds: parseLinks(match[2], "R", line.line, match[1]),
      acceptanceIds: parseLinks(match[3], "A", line.line, match[1]),
      text: match[4],
      trace: recordTrace(file, line.line)
    });
  }
  if (values.length === 0) fail2("BASELINE_VALUE_INVALID", "Tasks must contain at least one entry");
  return values;
}
function parseTestCommands(lines, file) {
  const records = [];
  const unique = /* @__PURE__ */ new Set();
  for (const line of lines) {
    if (line.text.trim() === "") continue;
    if (!line.text.startsWith("- ")) fail2("BASELINE_TEST_COMMAND_INVALID", "Test commands must be JSON argv array bullets", { line: line.line });
    let parsed;
    try {
      parsed = JSON.parse(line.text.slice(2));
    } catch {
      fail2("BASELINE_TEST_COMMAND_INVALID", "Test command must contain valid JSON", { line: line.line });
    }
    const argv = normalizeTestArgv(parsed);
    if (!argv) {
      fail2("BASELINE_TEST_COMMAND_INVALID", "Test command must be a safe nonempty argv array", { line: line.line });
    }
    const canonical = JSON.stringify(argv);
    if (unique.has(canonical)) fail2("BASELINE_TEST_COMMAND_INVALID", "Duplicate test command", { line: line.line });
    unique.add(canonical);
    records.push({ argv, trace: recordTrace(file, line.line) });
  }
  if (records.length === 0) fail2("BASELINE_TEST_COMMAND_INVALID", "At least one test command is required");
  return records;
}
function validateTrace(requirements, acceptance, tasks) {
  const requirementIds = new Set(requirements.map(({ id }) => id));
  const acceptanceById = new Map(acceptance.map((entry) => [entry.id, entry]));
  for (const entry of acceptance) {
    for (const id of entry.requirementIds) if (!requirementIds.has(id)) {
      fail2("BASELINE_TRACE_INVALID", `${entry.id} links unknown requirement ${id}`, { id: entry.id, link: id });
    }
  }
  for (const task of tasks) {
    for (const id of task.requirementIds) if (!requirementIds.has(id)) {
      fail2("BASELINE_TRACE_INVALID", `${task.id} links unknown requirement ${id}`, { id: task.id, link: id });
    }
    for (const id of task.acceptanceIds) if (!acceptanceById.has(id)) {
      fail2("BASELINE_TRACE_INVALID", `${task.id} links unknown acceptance ${id}`, { id: task.id, link: id });
    }
    const taskRequirements = new Set(task.requirementIds);
    const acceptanceRequirements = /* @__PURE__ */ new Set();
    for (const id of task.acceptanceIds) {
      for (const requirementId of acceptanceById.get(id).requirementIds) acceptanceRequirements.add(requirementId);
      if (!acceptanceById.get(id).requirementIds.some((requirementId) => taskRequirements.has(requirementId))) {
        fail2("BASELINE_TRACE_INVALID", `${task.id} does not overlap the requirements linked by ${id}`, { id: task.id, link: id });
      }
    }
    if (task.requirementIds.some((id) => !acceptanceRequirements.has(id)) || [...acceptanceRequirements].some((id) => !taskRequirements.has(id))) {
      fail2("BASELINE_TRACE_INVALID", `${task.id} has orphan requirement or acceptance links`, { id: task.id });
    }
  }
  const acceptedRequirements = new Set(acceptance.flatMap(({ requirementIds: ids }) => ids));
  const taskedRequirements = new Set(tasks.flatMap(({ requirementIds: ids }) => ids));
  const taskedAcceptance = new Set(tasks.flatMap(({ acceptanceIds: ids }) => ids));
  if (requirements.some(({ id }) => !acceptedRequirements.has(id) || !taskedRequirements.has(id)) || acceptance.some(({ id }) => !taskedAcceptance.has(id))) {
    fail2("BASELINE_TRACE_INVALID", "Requirements and acceptance entries must be covered by tasks");
  }
}
function stripMarkdownContainers(value) {
  let content2 = value;
  let previous;
  do {
    previous = content2;
    content2 = content2.replace(/^\s{0,3}>\s?/, "").replace(/^\s{0,3}(?:[-+*]|\d+[.)])\s+/, "");
  } while (content2 !== previous);
  return content2;
}
function rejectUnexpectedHeadings(sectionLines, section, fencedLines) {
  for (const line of sectionLines) {
    if (fencedLines.has(line.line - 1)) continue;
    const unwrapped = stripMarkdownContainers(line.text);
    const heading = /^\s{0,3}#{1,6}(?:\s|$)/.test(unwrapped);
    const setext = /^\s{0,3}(?:=+|-+)\s*$/.test(unwrapped);
    if ((heading || setext) && !(["Requirements", "Acceptance"].includes(section) && line.text.startsWith("### "))) {
      fail2("BASELINE_STRUCTURE_INVALID", `Unexpected heading in ${section}`, { line: line.line, section });
    }
  }
}
function fencedLineIndexes(rawLines) {
  const indexes = /* @__PURE__ */ new Set();
  let fence = null;
  for (let index = 0; index < rawLines.length; index++) {
    const line = rawLines[index];
    if (fence) {
      indexes.add(index);
      const closing = new RegExp(`^ {0,3}${fence.character}{${fence.length},}\\s*$`);
      if (closing.test(line)) fence = null;
      continue;
    }
    const opening = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (opening) {
      fence = { character: opening[1][0], length: opening[1].length };
      indexes.add(index);
    }
  }
  if (fence) fail2("BASELINE_STRUCTURE_INVALID", "Baseline contains an unterminated fenced code block");
  return indexes;
}
function parseFullBaseline(markdown, { file = "baseline.md" } = {}) {
  if (typeof markdown !== "string") fail2("BASELINE_STRUCTURE_INVALID", "Baseline Markdown must be text");
  let normalized = markdown.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
  if (CONTROL2.test(normalized)) fail2("BASELINE_VALUE_INVALID", "Baseline contains control characters");
  const rawLines = normalized.split("\n");
  const lines = rawLines.map((text2, index) => ({ file, line: index + 1, text: text2, section: null }));
  const fencedLines = fencedLineIndexes(rawLines);
  if (rawLines[0] !== "# Development Baseline") fail2("BASELINE_STRUCTURE_INVALID", "Baseline title must be exact and first");
  const headings = [];
  for (let index = 1; index < rawLines.length; index++) {
    const match = fencedLines.has(index) ? null : /^## (.+)$/.exec(rawLines[index]);
    if (match) headings.push({ name: match[1], index });
    else if (!fencedLines.has(index) && /^#{1,2}(?:\s|$)/.test(rawLines[index])) {
      fail2("BASELINE_STRUCTURE_INVALID", "Malformed top-level baseline heading", { line: index + 1 });
    }
  }
  if (headings.length !== FULL_BASELINE_SECTIONS.length || headings.some((heading, index) => heading.name !== FULL_BASELINE_SECTIONS[index])) {
    fail2("BASELINE_STRUCTURE_INVALID", "Baseline sections must be exact, unique, and ordered");
  }
  if (rawLines.slice(1, headings[0].index).some((line) => line.trim() !== "")) {
    fail2("BASELINE_STRUCTURE_INVALID", "Content is not allowed before Goal");
  }
  const sectionMap = /* @__PURE__ */ new Map();
  for (let index = 0; index < headings.length; index++) {
    const heading = headings[index];
    const end = headings[index + 1]?.index ?? rawLines.length;
    for (let cursor = heading.index; cursor < end; cursor++) lines[cursor].section = heading.name;
    const sectionLines = lines.slice(heading.index + 1, end);
    rejectUnexpectedHeadings(sectionLines, heading.name, fencedLines);
    sectionMap.set(heading.name, sectionLines);
  }
  const goal = content(sectionMap.get("Goal"), "Goal").text;
  const background = content(sectionMap.get("Background"), "Background").text;
  const scope = content(sectionMap.get("Scope"), "Scope").text;
  const nonGoals = content(sectionMap.get("Non-Goals"), "Non-Goals").text;
  const requirements = parseRequirements(sectionMap.get("Requirements"), file, fencedLines);
  const acceptance = parseAcceptance(sectionMap.get("Acceptance"), file, fencedLines);
  const tasks = parseTasks(sectionMap.get("Tasks"), file);
  const risks = content(sectionMap.get("Risks"), "Risks").text;
  const testCommandRecords = parseTestCommands(sectionMap.get("Test Commands"), file);
  const decisions = content(sectionMap.get("Decisions"), "Decisions").text;
  validateTrace(requirements, acceptance, tasks);
  return {
    schemaVersion: BASELINE_SCHEMA_VERSION,
    generatorVersion: BASELINE_GENERATOR_VERSION,
    goal,
    background,
    scope,
    nonGoals,
    requirements,
    acceptance,
    tasks,
    risks,
    testCommands: testCommandRecords.map(({ argv }) => argv),
    testCommandRecords,
    decisions,
    sourceLines: lines
  };
}

// src/baseline/sources.mjs
var SECRET_DIRECTORY = /^(?:\.git|\.ssh|\.gnupg|credentials?(?:[._-].*)?|secrets?(?:[._-].*)?|private[._-]?keys?)$/i;
var AWS_DIRECTORY = /^\.aws(?:$|-)/i;
var ENV_FILE = /^(?:\.env(?:$|[._-])|\.envrc$)|\.env$/i;
var CONFIG_EXTENSIONS = /* @__PURE__ */ new Set([
  "env",
  "yml",
  "yaml",
  "json",
  "toml",
  "ini",
  "conf",
  "config",
  "properties",
  "xml",
  "cnf",
  "cfg"
]);
var SENSITIVE_CONTENT_EXTENSIONS = /* @__PURE__ */ new Set([...CONFIG_EXTENSIONS, "csv", "tsv", "txt"]);
var KEY_EXTENSIONS = /* @__PURE__ */ new Set([
  "key",
  "pem",
  "p12",
  "pfx",
  "ppk",
  "jks",
  "jceks",
  "bks",
  "ks",
  "kdbx",
  "keystore",
  "private-key"
]);
var ENVIRONMENT_TOKEN = /(?:^|[._-])(?:prod|production|pre|preprod|preproduction|staging)(?:[._-]|$)/i;
var SENSITIVE_DOTFILE = /^(?:\.npmrc|\.pypirc|\.netrc|\.yarnrc(?:\.yml)?)$/i;
var SENSITIVE_BARE_FILE = /^\.?(?:credentials?|secrets?|passwords?|tokens?|api[._-]?keys?|service[._-]?accounts?|client[._-]?secrets?|private[._-]?keys?|keystores?)$/i;
var SENSITIVE_STEM = /(?:^|[._-])(?:credentials?|secrets?|passwords?|tokens?|api[._-]?keys?|service[._-]?accounts?|client[._-]?secrets?|private[._-]?keys?|keystores?|access[._-]?tokens?)(?:[._-](?:prod|production|pre|preprod|preproduction|staging))?$/i;
var SSH_KEY_FILE = /^id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?(?:\.(?:pub|bak|old|orig|backup))*~?$/i;
var BACKUP_SUFFIX = /\.(?:bak|old|orig|backup)~?$/i;
function forbiddenDirectory(segment) {
  return SECRET_DIRECTORY.test(segment) || AWS_DIRECTORY.test(segment) || ENV_FILE.test(segment);
}
function withoutBackupSuffixes(value) {
  let result3 = value;
  while (BACKUP_SUFFIX.test(result3)) result3 = result3.replace(BACKUP_SUFFIX, "");
  return result3;
}
function sensitiveCloudConfig(segments) {
  const normalized = [...segments];
  normalized[normalized.length - 1] = withoutBackupSuffixes(normalized.at(-1));
  const candidate = normalized.join("/");
  return /(?:^|\/)\.docker\/config\.json$/i.test(candidate) || /(?:^|\/)\.kube\/config$/i.test(candidate) || /(?:^|\/)\.azure\/accesstokens\.json$/i.test(candidate);
}
function forbiddenBasename(basename) {
  const name = withoutBackupSuffixes(basename);
  if (ENV_FILE.test(name) || SENSITIVE_DOTFILE.test(name) || SSH_KEY_FILE.test(basename) || SENSITIVE_BARE_FILE.test(name)) return true;
  const extension = path7.posix.extname(name).slice(1).toLowerCase();
  if (KEY_EXTENSIONS.has(extension)) return true;
  if (!SENSITIVE_CONTENT_EXTENSIONS.has(extension)) return false;
  const stem = name.slice(0, -(extension.length + 1));
  return SENSITIVE_STEM.test(stem);
}
function configEnvironmentPath(segments) {
  const basename = withoutBackupSuffixes(segments.at(-1));
  const extension = path7.posix.extname(basename).slice(1).toLowerCase();
  if (!CONFIG_EXTENSIONS.has(extension)) return false;
  const stem = basename.slice(0, -(extension.length + 1));
  return [...segments.slice(0, -1), stem].some((segment) => ENVIRONMENT_TOKEN.test(segment));
}
function canonicalJson3(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson3).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson3(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
function comparePath(left, right) {
  return left.path < right.path ? -1 : left.path > right.path ? 1 : 0;
}
function sourceManifestFingerprint(files) {
  const canonical = [...files].map(({ path: filePath, sha256, purpose }) => ({ path: filePath, sha256, purpose })).sort(comparePath);
  return sha256Bytes(Buffer.from(canonicalJson3(canonical), "utf8"));
}
function sameSourceSnapshots(left, right) {
  return Array.isArray(left?.entries) && Array.isArray(right?.entries) && left.entries.length === right.entries.length && left.entries.every((entry, index) => entry.path === right.entries[index].path && sameFileSnapshot(entry.snapshot, right.entries[index].snapshot));
}
function invalid2(message, details = {}) {
  throw new GatedLoopError("BASELINE_PATH_INVALID", message, { details });
}
function normalizeBaselineInputPath(candidate) {
  if (typeof candidate !== "string" || candidate.length === 0 || /[\u0000-\u001F\u007F]/.test(candidate) || /[*?\[\]{}<>"|]/.test(candidate) || path7.posix.isAbsolute(candidate) || path7.win32.isAbsolute(candidate) || /^[\\/]{2}/.test(candidate) || candidate.includes(":")) {
    invalid2("Baseline inputs must be explicit repository-relative paths", { path: candidate });
  }
  let normalized;
  try {
    normalized = canonicalRelativePath(candidate);
  } catch {
    invalid2("Baseline input escapes the repository", { path: candidate });
  }
  const segments = normalized.split("/");
  const lower = normalized.toLowerCase();
  if (normalized === "." || segments.includes("..") || lower === ".ai-dev-loop" || lower.startsWith(".ai-dev-loop/") || segments.some((segment) => /[. ]$/.test(segment)) || segments.slice(0, -1).some(forbiddenDirectory) || sensitiveCloudConfig(segments) || forbiddenBasename(segments.at(-1)) || configEnvironmentPath(segments)) {
    invalid2("Baseline input path is forbidden", { path: candidate });
  }
  return normalized;
}
function decode(bytes, filePath) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new GatedLoopError("BASELINE_UTF8_INVALID", `Baseline input is not valid UTF-8: ${filePath}`);
  }
}
function decodeSupportingSource(bytes) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return void 0;
  }
}
async function readEntry(root, filePath, purpose, fs) {
  let verified;
  try {
    verified = await readSafeRegularFileSnapshot(root, filePath, { fs });
  } catch (error) {
    if (error.code === "ENOENT") throw new GatedLoopError("BASELINE_PATH_INVALID", `Baseline input does not exist: ${filePath}`);
    throw error;
  }
  const { bytes, snapshot } = verified;
  const text2 = purpose === "baseline" ? decode(bytes, filePath) : decodeSupportingSource(bytes);
  return {
    path: filePath,
    purpose,
    bytes,
    text: text2,
    snapshot,
    sha256: sha256Bytes(bytes),
    lines: text2 === void 0 ? [] : text2.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").split("\n").map((lineText, index) => ({
      file: filePath,
      line: index + 1,
      text: lineText
    }))
  };
}
async function readBaselineSources({ root, baseline, sources = [], fs = fsPromises4 } = {}) {
  if (typeof root !== "string" || root.length === 0) throw new GatedLoopError("BASELINE_ROOT_INVALID", "Project root is required");
  if (!Array.isArray(sources)) throw new GatedLoopError("BASELINE_SOURCE_INVALID", "sources must be an array");
  const baselinePath = normalizeBaselineInputPath(baseline);
  if (!/\.md$/i.test(baselinePath)) throw new GatedLoopError("BASELINE_PATH_INVALID", "Baseline input must be a Markdown file");
  const sourcePaths2 = sources.map(normalizeBaselineInputPath);
  const allPaths = [baselinePath, ...sourcePaths2];
  const canonicalKeys = allPaths.map((entry) => entry.toLowerCase());
  if (new Set(canonicalKeys).size !== canonicalKeys.length) {
    throw new GatedLoopError("BASELINE_SOURCE_INVALID", "Baseline input paths must be unique");
  }
  const baselineEntry = await readEntry(root, baselinePath, "baseline", fs);
  const sourceEntries = [];
  for (const sourcePath of [...sourcePaths2].sort()) sourceEntries.push(await readEntry(root, sourcePath, "source", fs));
  const entries = [baselineEntry, ...sourceEntries];
  const identities = /* @__PURE__ */ new Set();
  for (const entry of entries) {
    if (entry.snapshot.ino !== 0n && entry.snapshot.ino !== 0) {
      const identity = `${entry.snapshot.dev}:${entry.snapshot.ino}`;
      if (identities.has(identity)) throw new GatedLoopError("BASELINE_SOURCE_INVALID", "Baseline inputs must identify distinct files");
      identities.add(identity);
    }
  }
  for (const entry of entries) {
    const verified = await readSafeRegularFileSnapshot(root, entry.path, { fs });
    if (!sameFileSnapshot(entry.snapshot, verified.snapshot) || sha256Bytes(verified.bytes) !== entry.sha256) {
      throw new GatedLoopError("PATH_FILE_CHANGED", `File changed while sources were being read: ${entry.path}`);
    }
  }
  const files = entries.map(({ path: filePath, sha256, purpose }) => ({ path: filePath, sha256, purpose }));
  const manifest = {
    schemaVersion: BASELINE_SCHEMA_VERSION,
    generatorVersion: BASELINE_GENERATOR_VERSION,
    files,
    fingerprint: sourceManifestFingerprint(files)
  };
  return { baseline: baselineEntry, sources: sourceEntries, entries, manifest };
}

// src/work-items/model.mjs
var WORK_ITEM_SCHEMA_VERSION = 2;
var WORK_ITEM_KINDS = Object.freeze(["DELIVERY", "CAPABILITY", "TASK"]);
var WORK_ITEM_AUTHORITIES = Object.freeze({
  DELIVERY: "COORDINATION",
  CAPABILITY: "COORDINATION",
  TASK: "EXECUTION"
});
var ITEM_ID = /^[a-z0-9][a-z0-9._-]*$/;
var TRACE_ID = /^(?:R|A)-(?:00[1-9]|0[1-9]\d|[1-9]\d{2})$/;
var PLACEHOLDER2 = /\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?/i;
var CONTROL3 = /[\u0000-\u001F\u007F-\u009F]/;
function fail3(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}
function exactKeys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return canonicalJson3(Object.keys(value).sort()) === canonicalJson3([...expected].sort());
}
function text(value, field) {
  if (typeof value !== "string" || value.trim().length === 0 || PLACEHOLDER2.test(value) || CONTROL3.test(value)) {
    fail3("WORK_ITEM_VALUE_INVALID", `${field} must be nonempty text without placeholders`, { field });
  }
  return value.trim();
}
function safeId(value, field = "id") {
  const reserved = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/;
  if (typeof value !== "string" || !ITEM_ID.test(value) || value.endsWith(".") || reserved.test(value)) {
    fail3("WORK_ITEM_ID_INVALID", `${field} must be a safe lowercase identifier`, { field, value });
  }
  return value;
}
function strings(values, field, { allowEmpty = false } = {}) {
  if (!Array.isArray(values) || !allowEmpty && values.length === 0) {
    fail3("WORK_ITEM_VALUE_INVALID", `${field} must be ${allowEmpty ? "an" : "a nonempty"} array`, { field });
  }
  const normalized = values.map((value, index) => text(value, `${field}[${index}]`));
  if (new Set(normalized).size !== normalized.length) {
    fail3("WORK_ITEM_VALUE_INVALID", `${field} contains duplicate values`, { field });
  }
  return normalized;
}
function normalizeScopePattern(value) {
  const normalized = text(value, "scope").replaceAll("\\", "/");
  const segments = normalized.split("/");
  const wildcard = /[?*{}[\]]/;
  const supportedPattern = !wildcard.test(normalized) || normalized.endsWith("/**") && !wildcard.test(normalized.slice(0, -3));
  const invalid3 = path8.posix.isAbsolute(normalized) || path8.win32.isAbsolute(normalized) || segments.includes("..") || normalized.includes(":") || normalized.startsWith(".hierarchical-delivery-governance/") || normalized === ".hierarchical-delivery-governance" || !supportedPattern;
  if (invalid3) fail3("WORK_ITEM_SCOPE_INVALID", "Scope contains an unsafe path pattern", { pattern: value });
  return normalized.replace(/^\.\//, "");
}
function normalizeScope2(values) {
  const normalized = strings(values, "scope").map(normalizeScopePattern);
  return [...new Set(normalized)].sort();
}
function traceRecords(values, prefix, field) {
  if (!Array.isArray(values) || values.length === 0) {
    fail3("WORK_ITEM_TRACE_INVALID", `${field} must be a nonempty array`, { field });
  }
  const seen = /* @__PURE__ */ new Set();
  return values.map((entry, index) => {
    const expectedKeys = prefix === "R" ? ["id", "text"] : ["id", "requirementIds", "expectedResult"];
    if (!exactKeys(entry, expectedKeys) || !TRACE_ID.test(entry.id) || !entry.id.startsWith(`${prefix}-`) || seen.has(entry.id)) {
      fail3("WORK_ITEM_TRACE_INVALID", `${field}[${index}] has an invalid or duplicate ID`, { field, index });
    }
    seen.add(entry.id);
    if (prefix === "R") return { id: entry.id, text: text(entry.text, `${field}.${entry.id}`) };
    return {
      id: entry.id,
      requirementIds: strings(entry.requirementIds, `${field}.${entry.id}.requirementIds`).sort(),
      expectedResult: text(entry.expectedResult, `${field}.${entry.id}`)
    };
  }).sort((left, right) => left.id.localeCompare(right.id));
}
function validateTrace2(requirements, acceptance) {
  const requirementIds = new Set(requirements.map(({ id }) => id));
  const accepted = /* @__PURE__ */ new Set();
  for (const entry of acceptance) {
    for (const id of entry.requirementIds) {
      if (!requirementIds.has(id)) fail3("WORK_ITEM_TRACE_INVALID", `${entry.id} references unknown requirement ${id}`);
      accepted.add(id);
    }
  }
  if (requirements.some(({ id }) => !accepted.has(id))) {
    fail3("WORK_ITEM_TRACE_INVALID", "Every requirement must be covered by acceptance");
  }
}
function childRecords(values, kind, requirements, acceptance) {
  if (!Array.isArray(values) || values.length === 0) {
    fail3("WORK_ITEM_CHILDREN_INVALID", `${kind} must declare at least one child work item`);
  }
  const expectedKind = kind === "DELIVERY" ? "CAPABILITY" : "TASK";
  const requirementIds = new Set(requirements.map(({ id }) => id));
  const acceptanceIds = new Set(acceptance.map(({ id }) => id));
  const seen = /* @__PURE__ */ new Set();
  return values.map((entry, index) => {
    const keys = ["id", "kind", "title", "requirementIds", "acceptanceIds"];
    if (!exactKeys(entry, keys) || entry.kind !== expectedKind) {
      fail3("WORK_ITEM_CHILDREN_INVALID", `${kind} children must be ${expectedKind} records`, { index });
    }
    const id = safeId(entry.id, `children[${index}].id`);
    if (seen.has(id)) fail3("WORK_ITEM_CHILDREN_INVALID", `Duplicate child ID: ${id}`);
    seen.add(id);
    const linkedRequirements = strings(entry.requirementIds, `${id}.requirementIds`).sort();
    const linkedAcceptance = strings(entry.acceptanceIds, `${id}.acceptanceIds`).sort();
    if (linkedRequirements.some((linked) => !requirementIds.has(linked)) || linkedAcceptance.some((linked) => !acceptanceIds.has(linked))) {
      fail3("WORK_ITEM_TRACE_INVALID", `${id} references unknown parent trace IDs`);
    }
    return {
      id,
      kind: expectedKind,
      title: text(entry.title, `${id}.title`),
      requirementIds: linkedRequirements,
      acceptanceIds: linkedAcceptance
    };
  }).sort((left, right) => left.id.localeCompare(right.id));
}
function executionRecord(value, id) {
  if (!exactKeys(value, ["dependsOn", "inputs", "outputs"])) {
    fail3("WORK_ITEM_EXECUTION_INVALID", "Task execution must contain dependsOn, inputs, and outputs");
  }
  const dependsOn = value.dependsOn.map((dependency, index) => safeId(dependency, `dependsOn[${index}]`));
  if (dependsOn.includes(id) || new Set(dependsOn).size !== dependsOn.length) {
    fail3("WORK_ITEM_DEPENDENCY_INVALID", "Task dependencies must be unique and cannot reference the Task itself");
  }
  return {
    dependsOn: [...dependsOn].sort(),
    inputs: strings(value.inputs, "execution.inputs", { allowEmpty: true }),
    outputs: strings(value.outputs, "execution.outputs")
  };
}
function decompositionRecord(value, kind, id, parent) {
  const expectedKeys = kind === "CAPABILITY" ? ["status", "dependsOn"] : ["status"];
  if (!exactKeys(value, expectedKeys) || !["OPEN", "SEALED"].includes(value.status)) {
    fail3("WORK_ITEM_DECOMPOSITION_INVALID", "Coordination work items require decomposition status OPEN or SEALED");
  }
  if (kind === "DELIVERY") return { status: value.status };
  if (!Array.isArray(value.dependsOn)) {
    fail3("WORK_ITEM_DEPENDENCY_INVALID", "Capability dependsOn must be an array");
  }
  const dependsOn = value.dependsOn.map((dependency, index) => safeId(dependency, `decomposition.dependsOn[${index}]`));
  const siblingIds = new Set(parent?.children?.filter(({ kind: childKind }) => childKind === "CAPABILITY").map(({ id: childId }) => childId));
  if (dependsOn.includes(id) || new Set(dependsOn).size !== dependsOn.length || dependsOn.some((dependency) => !siblingIds.has(dependency))) {
    fail3("WORK_ITEM_DEPENDENCY_INVALID", "Capability dependencies must be unique planned siblings and cannot reference itself");
  }
  return { status: value.status, dependsOn: [...dependsOn].sort() };
}
function testCommands(values) {
  if (!Array.isArray(values) || values.length === 0) {
    fail3("WORK_ITEM_TEST_COMMAND_INVALID", "At least one test command is required");
  }
  const commands = values.map((value) => normalizeTestArgv(value));
  if (commands.some((value) => !value)) fail3("WORK_ITEM_TEST_COMMAND_INVALID", "Test commands must be safe argv arrays");
  const canonical = commands.map((value) => JSON.stringify(value));
  if (new Set(canonical).size !== canonical.length) fail3("WORK_ITEM_TEST_COMMAND_INVALID", "Duplicate test command");
  return commands;
}
function scopeCovers(parentPattern, childPattern) {
  if (parentPattern === "**") return true;
  if (!parentPattern.endsWith("/**")) return parentPattern === childPattern;
  const prefix = parentPattern.slice(0, -3);
  return childPattern === prefix || childPattern.startsWith(`${prefix}/`);
}
function scopeContains(parentScope, childScope) {
  return childScope.every((childPattern) => parentScope.some((parentPattern) => scopeCovers(parentPattern, childPattern)));
}
function scopePatternsOverlap(left, right) {
  return left.some((leftPattern) => right.some((rightPattern) => scopeCovers(leftPattern, rightPattern) || scopeCovers(rightPattern, leftPattern)));
}
function normalizeParent(definition, parent) {
  if (definition.kind === "DELIVERY") {
    if (definition.parentId !== void 0 && definition.parentId !== null) {
      fail3("WORK_ITEM_PARENT_INVALID", "Delivery cannot have a parent work item");
    }
    return { parentId: null, parentContractFingerprint: null };
  }
  if (definition.parentId === null) {
    if (parent) fail3("WORK_ITEM_PARENT_INVALID", `Root ${definition.kind} cannot receive a parent contract`);
    if (definition.kind === "TASK" && definition.execution.dependsOn.length > 0) {
      fail3("WORK_ITEM_DEPENDENCY_INVALID", "A root Task cannot depend on sibling Tasks; use a Capability root");
    }
    if (definition.kind === "CAPABILITY" && definition.decomposition.dependsOn.length > 0) {
      fail3("WORK_ITEM_DEPENDENCY_INVALID", "A root Capability cannot depend on sibling Capabilities; use a Delivery root");
    }
    return { parentId: null, parentContractFingerprint: null };
  }
  if (!parent || definition.parentId !== parent.id) {
    fail3("WORK_ITEM_PARENT_INVALID", `${definition.kind} must reference its supplied parent`);
  }
  const expectedParentKind = definition.kind === "CAPABILITY" ? "DELIVERY" : "CAPABILITY";
  if (parent.kind !== expectedParentKind) {
    fail3("WORK_ITEM_PARENT_INVALID", `${definition.kind} parent must be ${expectedParentKind}`);
  }
  const planned = parent.children?.find(({ id, kind }) => id === definition.id && kind === definition.kind);
  if (!planned) fail3("WORK_ITEM_PARENT_PLAN_MISMATCH", `${definition.id} is not declared by its parent baseline`);
  if (!scopeContains(parent.scope, definition.scope)) {
    fail3("WORK_ITEM_SCOPE_EXPANDED", `${definition.id} scope expands beyond its parent baseline`);
  }
  return {
    parentId: parent.id,
    parentContractFingerprint: workItemChildContractFingerprint(parent, definition.id)
  };
}
function validateWorkItemDefinition(definition, { parent } = {}) {
  if (!definition || typeof definition !== "object" || Array.isArray(definition)) {
    fail3("WORK_ITEM_DEFINITION_INVALID", "Work item definition must be an object");
  }
  if (!WORK_ITEM_KINDS.includes(definition.kind)) {
    fail3("WORK_ITEM_KIND_INVALID", "Work item kind must be DELIVERY, CAPABILITY, or TASK");
  }
  if (definition.schemaVersion !== WORK_ITEM_SCHEMA_VERSION) {
    fail3("WORK_ITEM_SCHEMA_INVALID", `Work item schemaVersion must be ${WORK_ITEM_SCHEMA_VERSION}`);
  }
  if (definition.kind === "TASK" && Object.hasOwn(definition, "children")) {
    fail3("WORK_ITEM_TASK_NOT_LEAF", "Task is an executable leaf and cannot contain children");
  }
  if (definition.kind !== "TASK" && Object.hasOwn(definition, "execution")) {
    fail3("WORK_ITEM_EXECUTION_INVALID", "Only Task work items can contain execution metadata");
  }
  const commonKeys = [
    "schemaVersion",
    "id",
    "kind",
    "title",
    "goal",
    "scope",
    "nonGoals",
    "requirements",
    "acceptance",
    "testCommands",
    "risks",
    "decisions"
  ];
  const expectedKeys = definition.kind === "DELIVERY" ? [...commonKeys, "decomposition", "children"] : [...commonKeys, "parentId", ...definition.kind === "TASK" ? ["execution"] : ["decomposition", "children"]];
  if (!exactKeys(definition, expectedKeys)) {
    fail3("WORK_ITEM_DEFINITION_INVALID", "Work item definition contains missing or unknown fields", {
      expectedKeys: expectedKeys.sort(),
      actualKeys: Object.keys(definition).sort()
    });
  }
  const normalized = {
    schemaVersion: WORK_ITEM_SCHEMA_VERSION,
    id: safeId(definition.id),
    kind: definition.kind,
    authorityKind: WORK_ITEM_AUTHORITIES[definition.kind],
    title: text(definition.title, "title"),
    goal: text(definition.goal, "goal"),
    scope: normalizeScope2(definition.scope),
    nonGoals: strings(definition.nonGoals, "nonGoals"),
    requirements: traceRecords(definition.requirements, "R", "requirements"),
    acceptance: traceRecords(definition.acceptance, "A", "acceptance"),
    testCommands: testCommands(definition.testCommands),
    risks: strings(definition.risks, "risks"),
    decisions: strings(definition.decisions, "decisions")
  };
  validateTrace2(normalized.requirements, normalized.acceptance);
  if (definition.kind === "TASK") normalized.execution = executionRecord(definition.execution, normalized.id);
  else {
    normalized.decomposition = decompositionRecord(definition.decomposition, definition.kind, normalized.id, parent);
    normalized.children = childRecords(definition.children, definition.kind, normalized.requirements, normalized.acceptance);
  }
  Object.assign(normalized, normalizeParent({ ...definition, ...normalized }, parent));
  return normalized;
}
function contract(definition) {
  const normalized = {
    schemaVersion: definition.schemaVersion,
    id: definition.id,
    kind: definition.kind,
    goal: definition.goal,
    scope: [...definition.scope].sort(),
    requirements: [...definition.requirements].sort((left, right) => left.id.localeCompare(right.id)),
    acceptance: [...definition.acceptance].sort((left, right) => left.id.localeCompare(right.id)),
    testCommands: definition.testCommands
  };
  if (definition.children) normalized.children = [...definition.children].sort((left, right) => left.id.localeCompare(right.id));
  if (definition.decomposition) normalized.decomposition = definition.decomposition;
  if (definition.execution) normalized.execution = definition.execution;
  return normalized;
}
function workItemContractFingerprint(definition) {
  return sha256Bytes(Buffer.from(canonicalJson3(contract(definition)), "utf8"));
}
function workItemChildContractFingerprint(parent, childId) {
  const child = parent.children?.find(({ id }) => id === childId);
  if (!child) fail3("WORK_ITEM_PARENT_PLAN_MISMATCH", `${childId} is not declared by its parent baseline`);
  const stableParentContract = contract(parent);
  delete stableParentContract.children;
  delete stableParentContract.decomposition;
  return sha256Bytes(Buffer.from(canonicalJson3({
    parent: stableParentContract,
    child
  }), "utf8"));
}
function workItemBaselineFingerprint(definition) {
  return sha256Bytes(Buffer.from(canonicalJson3(definition), "utf8"));
}
function list(values) {
  return values.map((value) => `- ${value}`).join("\n");
}
function renderWorkItemBaseline(definition) {
  const lines = [
    "# Work Item Baseline",
    "",
    `Work Item: ${definition.id}`,
    `Kind: ${definition.kind}`,
    `Authority: ${definition.authorityKind}`,
    `Parent: ${definition.parentId ?? "none"}`,
    `Parent Contract: ${definition.parentContractFingerprint ?? "none"}`,
    "",
    "## Goal",
    definition.goal,
    "",
    "## Scope",
    list(definition.scope),
    "",
    "## Non-Goals",
    list(definition.nonGoals),
    "",
    "## Requirements"
  ];
  for (const requirement of definition.requirements) lines.push(`### ${requirement.id}`, requirement.text, "");
  lines.push("## Acceptance");
  for (const acceptance of definition.acceptance) {
    lines.push(`### ${acceptance.id} [${acceptance.requirementIds.join(",")}]`, acceptance.expectedResult, "");
  }
  if (definition.children) {
    lines.push(
      "## Decomposition",
      `- Status: ${definition.decomposition.status}`,
      ...definition.kind === "CAPABILITY" ? [`- Capability dependencies: ${definition.decomposition.dependsOn.join(", ") || "none"}`] : [],
      "",
      "## Children"
    );
    for (const child of definition.children) {
      lines.push(`- ${child.id} [${child.kind}] [${child.requirementIds.join(",")}] [${child.acceptanceIds.join(",")}] ${child.title}`);
    }
  } else {
    lines.push(
      "## Execution",
      `- Depends on: ${definition.execution.dependsOn.join(", ") || "none"}`,
      `- Inputs: ${definition.execution.inputs.join("; ") || "none"}`,
      `- Outputs: ${definition.execution.outputs.join("; ")}`
    );
  }
  lines.push("", "## Test Commands", ...definition.testCommands.map((argv) => `- ${JSON.stringify(argv)}`));
  lines.push("", "## Risks", list(definition.risks));
  lines.push("", "## Decisions", list(definition.decisions), "");
  return lines.join("\n");
}
function resolveSelfHostingPolicy({ packageName, explicitDogfood = false } = {}) {
  const implementationPackages = /* @__PURE__ */ new Set(["hierarchical-delivery-governance"]);
  if (implementationPackages.has(packageName) && explicitDogfood !== true) {
    return {
      route: "SELF_HOSTING_MAINTENANCE",
      createsRuntimePackage: false,
      reason: "HIERARCHICAL_GOVERNANCE_SELF_MAINTENANCE"
    };
  }
  return {
    route: "STANDARD_HIERARCHICAL_GOVERNANCE",
    createsRuntimePackage: true,
    reason: explicitDogfood === true ? "EXPLICIT_DOGFOOD" : "NOT_SELF_HOSTING"
  };
}

// src/commands/start.mjs
function deterministicTaskId(description) {
  const canonicalDescription = typeof description === "string" ? description.normalize("NFC") : "";
  return `task-${sha256Bytes(Buffer.from(canonicalDescription, "utf8")).slice(0, 20)}`;
}
async function resolvePersistenceTask(task, route, generateTaskId) {
  if (task !== void 0) return task;
  if (generateTaskId !== void 0 && typeof generateTaskId !== "function") {
    throw new GatedLoopError("TASK_ID_GENERATOR_INVALID", "Task ID generator must be a function");
  }
  const generator = generateTaskId ?? deterministicTaskId;
  return generator(route.evaluatedInputs.description);
}
async function resolveStartPolicy(root, explicitDogfood, fs) {
  let packageName;
  if (root) {
    try {
      const packageJson = JSON.parse((await readSafeRegularFile(root, "package.json", { fs })).toString("utf8"));
      if (typeof packageJson?.name === "string") packageName = packageJson.name;
    } catch (error) {
      if (error?.code !== "ENOENT" && error?.code !== "PATH_MISSING") throw error;
    }
  }
  return resolveSelfHostingPolicy({
    packageName,
    explicitDogfood
  });
}
async function startTask({
  root,
  task,
  signals,
  brief,
  confirmed = false,
  explicitDogfood = false,
  hostRuntime: suppliedHostRuntime,
  generateTaskId,
  now,
  beforeCommit,
  fs = fsPromises5
} = {}) {
  const hostRuntime = normalizeHostRuntime(suppliedHostRuntime);
  const host = hostRuntime ? { hostRuntime } : {};
  const route = routeTask(signals);
  if (route.mode === "none") return { route, nextAction: "none", artifacts: [], ...host };
  const policy = await resolveStartPolicy(root, explicitDogfood, fs);
  if (policy.createsRuntimePackage === false) {
    return { route, policy, nextAction: "self-hosting-maintenance", artifacts: [], ...host };
  }
  if (route.mode === "full") {
    const resolvedTask2 = await resolvePersistenceTask(task, route, generateTaskId);
    const persistence = await persistFullMode({ root, task: resolvedTask2, classification: route, hostRuntime, now, beforeCommit, fs });
    return { route, task: resolvedTask2, nextAction: "prepare", authority: "generic-baseline", persistence, artifacts: persistence.artifacts, ...host };
  }
  if (confirmed === true && brief === void 0) throw new GatedLoopError("LIGHT_BRIEF_REQUIRED", "Confirmed Light start requires an injected brief");
  if (confirmed !== true) {
    return {
      route,
      nextAction: "confirm",
      brief: brief === void 0 ? null : buildLightBrief(brief),
      artifacts: [],
      ...host
    };
  }
  buildLightBrief(brief);
  const resolvedTask = await resolvePersistenceTask(task, route, generateTaskId);
  const frozen = await freezeLightTask({ root, task: resolvedTask, classification: route, brief, confirmed, hostRuntime, now, beforeCommit, fs });
  return { route, task: resolvedTask, nextAction: "develop", freeze: frozen, artifacts: frozen.artifacts, ...host };
}

// src/full/freeze.mjs
import * as fsPromises7 from "node:fs/promises";
import path10 from "node:path";

// src/baseline/artifacts.mjs
function buildBaselineArtifacts(model, { baselineFingerprint } = {}) {
  if (!model || !Array.isArray(model.acceptance) || !Array.isArray(model.tasks)) {
    throw new GatedLoopError("BASELINE_MODEL_INVALID", "Baseline model is required");
  }
  return {
    acceptance: {
      schemaVersion: BASELINE_SCHEMA_VERSION,
      generatorVersion: BASELINE_GENERATOR_VERSION,
      ...baselineFingerprint ? { baselineFingerprint } : {},
      acceptance: model.acceptance.map(({ id, requirementIds, expectedResult, trace }) => ({
        id,
        requirementIds: [...requirementIds],
        expectedResult,
        trace: { ...trace }
      }))
    },
    tasks: {
      schemaVersion: BASELINE_SCHEMA_VERSION,
      generatorVersion: BASELINE_GENERATOR_VERSION,
      ...baselineFingerprint ? { baselineFingerprint } : {},
      tasks: model.tasks.map(({ id, requirementIds, acceptanceIds, text: text2, trace }) => ({
        id,
        requirementIds: [...requirementIds],
        acceptanceIds: [...acceptanceIds],
        text: text2,
        trace: { ...trace }
      }))
    }
  };
}

// src/baseline/render.mjs
function block(value, field) {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new GatedLoopError("BASELINE_MODEL_INVALID", `${field} must be nonempty`);
  }
  return value.replace(/\r\n?/g, "\n").replace(/^\n+|\n+$/g, "");
}
function list2(value, field) {
  if (!Array.isArray(value) || value.length === 0) throw new GatedLoopError("BASELINE_MODEL_INVALID", `${field} must be nonempty`);
  return value;
}
function renderFullBaseline(model) {
  if (!model || typeof model !== "object" || Array.isArray(model) || model.schemaVersion !== BASELINE_SCHEMA_VERSION || model.generatorVersion !== BASELINE_GENERATOR_VERSION) {
    throw new GatedLoopError("BASELINE_MODEL_INVALID", "Baseline model has an unsupported schema");
  }
  const requirements = list2(model.requirements, "requirements").flatMap((entry) => [
    `### ${entry.id} ${entry.title}`,
    block(entry.text, entry.id),
    ""
  ]);
  const acceptance = list2(model.acceptance, "acceptance").flatMap((entry) => [
    `### ${entry.id} [${entry.requirementIds.join(",")}]`,
    block(entry.expectedResult, entry.id),
    ""
  ]);
  const tasks = list2(model.tasks, "tasks").map((entry) => `- [ ] ${entry.id} [${entry.requirementIds.join(",")}] [${entry.acceptanceIds.join(",")}] ${entry.text}`);
  const testCommands2 = list2(model.testCommands, "testCommands").map((argv) => `- ${JSON.stringify(argv)}`);
  return [
    "# Development Baseline",
    "",
    "## Goal",
    block(model.goal, "goal"),
    "",
    "## Background",
    block(model.background, "background"),
    "",
    "## Scope",
    block(model.scope, "scope"),
    "",
    "## Non-Goals",
    block(model.nonGoals, "nonGoals"),
    "",
    "## Requirements",
    ...requirements,
    "## Acceptance",
    ...acceptance,
    "## Tasks",
    ...tasks,
    "",
    "## Risks",
    block(model.risks, "risks"),
    "",
    "## Test Commands",
    ...testCommands2,
    "",
    "## Decisions",
    block(model.decisions, "decisions"),
    ""
  ].join("\n");
}

// src/full/package.mjs
import * as fsPromises6 from "node:fs/promises";
import path9 from "node:path";
var WAITING_FILES = Object.freeze([
  "acceptance.json",
  "baseline.md",
  "decision-log.md",
  "mode.json",
  "source-manifest.json",
  "state.json",
  "tasks.json"
]);
var FROZEN_FILES = Object.freeze([...WAITING_FILES, DEVELOPMENT_HANDOFF_FILE].sort());
var DEFAULT_DECISION_LOG = "# Decision Log\n\nNo additional decisions recorded.\n";
function json4(value) {
  return `${JSON.stringify(value, null, 2)}
`;
}
function timestamp3(now) {
  const value = typeof now === "function" ? now() : /* @__PURE__ */ new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) throw new GatedLoopError("BASELINE_TIMESTAMP_INVALID", "Baseline timestamp is invalid");
  return date.toISOString();
}
function validateTask3(task) {
  const reserved = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/;
  if (typeof task !== "string" || !/^[a-z0-9][a-z0-9._-]*$/.test(task) || task.endsWith(".") || reserved.test(task)) {
    throw new GatedLoopError("BASELINE_TASK_INVALID", "Task must be a safe single path segment");
  }
}
function same(left, right) {
  return canonicalJson3(left) === canonicalJson3(right);
}
function canonicalTimestamp(value) {
  if (typeof value !== "string") return false;
  const date = new Date(value);
  return !Number.isNaN(date.valueOf()) && date.toISOString() === value;
}
function frozenMetadata(state) {
  return {
    schemaVersion: state.schemaVersion,
    task: state.task,
    mode: state.mode,
    stage: state.stage,
    hostRuntime: state.hostRuntime,
    reviewer: state.reviewer,
    sourceFingerprint: state.sourceFingerprint,
    baselineFingerprint: state.baselineFingerprint,
    inputFingerprint: state.inputFingerprint,
    updatedAt: state.updatedAt
  };
}
function frozenStateFingerprint(state, artifactHashSet = state.artifactHashes) {
  return sha256Bytes(Buffer.from(canonicalJson3({
    artifactHashes: artifactHashSet,
    state: frozenMetadata(state)
  }), "utf8"));
}
function renderFullHandoff(model, task, hostRuntime) {
  return renderDevelopmentHandoff({
    task,
    reviewer: hostRuntime,
    authorityFile: "baseline.md",
    scope: model.scope,
    tasks: model.tasks,
    acceptance: model.acceptance,
    testCommands: model.testCommands
  });
}
var FROZEN_HASH_NAMES = Object.freeze([
  "acceptance.json",
  "baseline.md",
  "decision-log.md",
  DEVELOPMENT_HANDOFF_FILE,
  "mode.json",
  "source-manifest.json",
  "tasks.json"
]);
function deriveFrozenPackage(taskPackage, handoff, handoffName = DEVELOPMENT_HANDOFF_FILE) {
  const files = {
    "mode.json": taskPackage.modeBytes,
    "baseline.md": taskPackage.bytes["baseline.md"],
    "acceptance.json": taskPackage.bytes["acceptance.json"],
    "tasks.json": taskPackage.bytes["tasks.json"],
    "source-manifest.json": taskPackage.bytes["source-manifest.json"],
    "decision-log.md": taskPackage.bytes["decision-log.md"],
    [handoffName]: handoff
  };
  const hashNames = FROZEN_HASH_NAMES.map((name) => name === DEVELOPMENT_HANDOFF_FILE ? handoffName : name);
  return { files, hashes: artifactHashes2(files, hashNames) };
}
function validateFrozenPackage(taskPackage, handoff) {
  const handoffName = taskPackage.handoffName ?? DEVELOPMENT_HANDOFF_FILE;
  const { hashes } = deriveFrozenPackage(taskPackage, handoff, handoffName);
  const valid = taskPackage.bytes[handoffName].toString("utf8") === handoff && canonicalJson3(taskPackage.state.artifactHashes) === canonicalJson3(hashes) && taskPackage.state.frozenFingerprint === frozenStateFingerprint(taskPackage.state, hashes);
  if (!valid) throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Frozen handoff or state metadata changed");
}
function validateMode(mode) {
  const modeKeys = ["classifierVersion", "mode", "reasons", "confidence", "evaluatedInputs", "hostRuntime", "createdAt"];
  const valid = mode && typeof mode === "object" && !Array.isArray(mode) && same(Object.keys(mode).sort(), modeKeys.sort()) && mode.classifierVersion === CLASSIFIER_VERSION && mode.mode === "full" && Array.isArray(mode.reasons) && ["high", "medium"].includes(mode.confidence) && mode.evaluatedInputs && typeof mode.evaluatedInputs === "object" && canonicalTimestamp(mode.createdAt);
  if (!valid) throw new GatedLoopError("FULL_MODE_REQUIRED", "A persisted Full mode is required");
  let replay;
  try {
    replay = classifyMode(mode.evaluatedInputs);
  } catch {
    throw new GatedLoopError("FULL_MODE_REQUIRED", "Persisted Full mode is invalid");
  }
  if (replay.mode !== "full" || !same(replay.reasons, mode.reasons) || replay.confidence !== mode.confidence || !same(replay.evaluatedInputs, mode.evaluatedInputs)) {
    throw new GatedLoopError("FULL_MODE_REQUIRED", "Persisted Full mode does not match its inputs");
  }
  try {
    return requireHostRuntime(mode.hostRuntime);
  } catch {
    throw new GatedLoopError("FULL_MODE_REQUIRED", "Persisted host runtime is invalid");
  }
}
function parseJson2(bytes, name) {
  try {
    return JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new GatedLoopError("BASELINE_SOURCE_CHANGED", `Existing ${name} is invalid`);
  }
}
async function readBytes(target, name, fs) {
  try {
    return await readSafeRegularFile(target, name, { fs });
  } catch (error) {
    if (error instanceof GatedLoopError) throw new GatedLoopError("BASELINE_SOURCE_CHANGED", `Existing artifact changed: ${name}`);
    throw error;
  }
}
function validateState(state, task, hostRuntime) {
  const validStage = ["WAITING_FOR_BASELINE_CONFIRMATION", "BASELINE_FROZEN"].includes(state?.stage);
  const stateKeys = [
    "schemaVersion",
    "task",
    "mode",
    "stage",
    "hostRuntime",
    "reviewer",
    "sourceFingerprint",
    "baselineFingerprint",
    "inputFingerprint",
    "artifactHashes",
    "updatedAt"
  ];
  if (state?.stage === "BASELINE_FROZEN") stateKeys.push("frozenFingerprint");
  const valid = state && typeof state === "object" && !Array.isArray(state) && same(Object.keys(state).sort(), stateKeys.sort()) && state.schemaVersion === 1 && state.task === task && state.mode === "full" && validStage && state.hostRuntime === hostRuntime && state.reviewer === hostRuntime && typeof state.sourceFingerprint === "string" && /^[a-f0-9]{64}$/.test(state.sourceFingerprint) && typeof state.baselineFingerprint === "string" && /^[a-f0-9]{64}$/.test(state.baselineFingerprint) && typeof state.inputFingerprint === "string" && /^[a-f0-9]{64}$/.test(state.inputFingerprint) && state.artifactHashes && typeof state.artifactHashes === "object" && !Array.isArray(state.artifactHashes) && canonicalTimestamp(state.updatedAt);
  if (!valid) throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Existing baseline state is invalid");
  const expectedInputFingerprint = sha256Bytes(Buffer.from(canonicalJson3({
    task,
    hostRuntime,
    sourceFingerprint: state.sourceFingerprint,
    baselineFingerprint: state.baselineFingerprint,
    modeSha256: state.artifactHashes["mode.json"]
  }), "utf8"));
  if (state.inputFingerprint !== expectedInputFingerprint) {
    throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Existing baseline input fingerprint is invalid");
  }
  if (state.stage === "BASELINE_FROZEN" && state.frozenFingerprint !== frozenStateFingerprint(state)) {
    throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Existing frozen fingerprint is invalid");
  }
}
function validateHashes(state, bytes, handoffName) {
  const expectedNames = state.stage === "BASELINE_FROZEN" ? ["acceptance.json", "baseline.md", "decision-log.md", handoffName, "mode.json", "source-manifest.json", "tasks.json"] : ["acceptance.json", "baseline.md", "mode.json", "source-manifest.json", "tasks.json"];
  if (!same(Object.keys(state.artifactHashes).sort(), [...expectedNames].sort())) {
    throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Existing artifact hash set is invalid");
  }
  for (const name of expectedNames) {
    if (!bytes[name] || state.artifactHashes[name] !== sha256Bytes(bytes[name])) {
      throw new GatedLoopError("BASELINE_SOURCE_CHANGED", `Existing artifact changed: ${name}`);
    }
  }
}
async function readFullPackage({ root, task, fs = fsPromises6 } = {}) {
  if (typeof root !== "string" || root.length === 0) throw new GatedLoopError("BASELINE_ROOT_INVALID", "Project root is required");
  validateTask3(task);
  const target = await assertSafePath(root, path9.join(".ai-dev-loop", task), { fs });
  let readableTarget;
  let stat;
  try {
    readableTarget = await resolveAtomicDirectory(target, { fs });
    stat = await fs.lstat(readableTarget);
  } catch (error) {
    if (error.code === "ENOENT") throw new GatedLoopError("FULL_MODE_REQUIRED", "Start must persist Full mode before prepare");
    throw error;
  }
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Task artifact path is invalid");
  const allNames = (await fs.readdir(readableTarget)).sort();
  const names = (await validateMutableRuntimeEntries(readableTarget, allNames, { fs })).sort();
  if (!names.includes("mode.json")) throw new GatedLoopError("FULL_MODE_REQUIRED", "A persisted Full mode is required");
  const modeBytes = await readBytes(readableTarget, "mode.json", fs);
  const mode = parseJson2(modeBytes, "mode.json");
  const hostRuntime = validateMode(mode);
  if (names.length === 1 && names[0] === "mode.json") {
    return { target, stage: null, mode, modeBytes, hostRuntime, bytes: { "mode.json": modeBytes } };
  }
  const stateBytes = await readBytes(readableTarget, "state.json", fs);
  const state = parseJson2(stateBytes, "state.json");
  const handoffName = state?.stage === "BASELINE_FROZEN" ? handoffFileFromNames(names) : null;
  const expected = state?.stage === "BASELINE_FROZEN" ? [...WAITING_FILES, handoffName].sort() : WAITING_FILES;
  if (!same(names, expected)) throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Existing baseline package is incomplete or has unexpected files");
  const bytes = { "state.json": stateBytes, "mode.json": modeBytes };
  for (const name of names) if (name !== "state.json" && name !== "mode.json") bytes[name] = await readBytes(readableTarget, name, fs);
  validateState(state, task, hostRuntime);
  validateHashes(state, bytes, handoffName);
  return {
    target,
    stage: state.stage,
    mode,
    modeBytes: bytes["mode.json"],
    hostRuntime,
    state,
    handoffName,
    bytes,
    manifest: parseJson2(bytes["source-manifest.json"], "source-manifest.json"),
    acceptance: parseJson2(bytes["acceptance.json"], "acceptance.json"),
    tasks: parseJson2(bytes["tasks.json"], "tasks.json")
  };
}
function artifactHashes2(files, names) {
  return Object.fromEntries([...names].sort().map((name) => [name, sha256Bytes(Buffer.isBuffer(files[name]) ? files[name] : Buffer.from(files[name], "utf8"))]));
}
async function replaceFullPackage(target, files, { fs = fsPromises6, beforeCommit } = {}) {
  await atomicReplaceDirectory(target, async (staging) => {
    for (const name of Object.keys(files).sort()) {
      await atomicWriteFile(path9.join(staging, name), files[name], { fs });
    }
  }, { fs, validateUnderLock: beforeCommit });
}
function fullOutcome(taskPackage, { created = false, updated = false } = {}) {
  const artifactNames = taskPackage.state?.stage === "BASELINE_FROZEN" ? [...WAITING_FILES, taskPackage.handoffName ?? DEVELOPMENT_HANDOFF_FILE].sort() : WAITING_FILES;
  return {
    created,
    updated,
    idempotent: !created && !updated,
    task: taskPackage.state?.task,
    mode: "full",
    stage: taskPackage.state?.stage,
    artifactDir: taskPackage.target,
    artifacts: artifactNames.map((name) => path9.join(taskPackage.target, name)),
    sourceFingerprint: taskPackage.state?.sourceFingerprint,
    baselineFingerprint: taskPackage.state?.baselineFingerprint,
    hostRuntime: taskPackage.hostRuntime,
    reviewer: taskPackage.hostRuntime
  };
}

// src/full/freeze.mjs
function sourcePaths(manifest) {
  const valid = manifest && typeof manifest === "object" && !Array.isArray(manifest) && manifest.schemaVersion === 1 && manifest.generatorVersion === 1 && Array.isArray(manifest.files) && manifest.files.length > 0 && manifest.files[0]?.purpose === "baseline" && manifest.files.slice(1).every((entry) => entry?.purpose === "source");
  if (!valid) throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Prepared source manifest is invalid");
  return { baseline: manifest.files[0].path, sources: manifest.files.slice(1).map(({ path: path16 }) => path16) };
}
function comparePrepared(taskPackage, sourceSet, model) {
  const baselineText = renderFullBaseline(model);
  const baselineFingerprint = sha256Bytes(Buffer.from(baselineText, "utf8"));
  const generated = buildBaselineArtifacts(model, { baselineFingerprint });
  const valid = taskPackage.state.sourceFingerprint === sourceSet.manifest.fingerprint && taskPackage.state.baselineFingerprint === baselineFingerprint && canonicalJson3(taskPackage.manifest) === canonicalJson3(sourceSet.manifest) && taskPackage.bytes["baseline.md"].toString("utf8") === baselineText && taskPackage.bytes["acceptance.json"].toString("utf8") === json4(generated.acceptance) && taskPackage.bytes["tasks.json"].toString("utf8") === json4(generated.tasks);
  if (!valid) throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Prepared baseline or sources changed");
  return baselineFingerprint;
}
function sameBytes(left, right) {
  return left && right && Buffer.compare(left, right) === 0;
}
async function freezeFullBaselineLocked({
  root,
  task,
  confirmed = false,
  now = () => /* @__PURE__ */ new Date(),
  beforeCommit,
  fs = fsPromises7
} = {}) {
  if (confirmed !== true) throw new GatedLoopError("CONFIRMATION_REQUIRED", "Baseline freeze requires explicit confirmation");
  const taskPackage = await readFullPackage({ root, task, fs });
  if (taskPackage.stage === null) throw new GatedLoopError("BASELINE_NOT_PREPARED", "Prepare the Full baseline before freezing");
  const paths = sourcePaths(taskPackage.manifest);
  let sourceSet;
  try {
    sourceSet = await readBaselineSources({ root, ...paths, fs });
  } catch {
    throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Prepared baseline sources changed");
  }
  let model;
  try {
    model = parseFullBaseline(sourceSet.baseline.text, { file: sourceSet.baseline.path });
  } catch {
    throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Prepared baseline semantics changed");
  }
  comparePrepared(taskPackage, sourceSet, model);
  const handoff = renderFullHandoff(model, task, taskPackage.hostRuntime);
  const { files, hashes } = deriveFrozenPackage(taskPackage, handoff);
  if (taskPackage.stage === "BASELINE_FROZEN") {
    validateFrozenPackage(taskPackage, handoff);
    return fullOutcome(taskPackage);
  }
  const state = {
    ...taskPackage.state,
    stage: "BASELINE_FROZEN",
    artifactHashes: hashes,
    updatedAt: timestamp3(now)
  };
  state.frozenFingerprint = frozenStateFingerprint(state, hashes);
  files["state.json"] = json4(state);
  const verifyBeforeCommit = async (staging) => {
    if (beforeCommit) await beforeCommit(staging);
    const current = await readFullPackage({ root, task, fs });
    if (current.stage !== taskPackage.stage || !sameBytes(current.bytes["state.json"], taskPackage.bytes["state.json"]) || !sameBytes(current.bytes["decision-log.md"], taskPackage.bytes["decision-log.md"])) {
      throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Task package changed during freeze");
    }
    let finalSources;
    try {
      finalSources = await readBaselineSources({ root, ...paths, fs });
    } catch {
      throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Baseline source changed during freeze");
    }
    if (finalSources.manifest.fingerprint !== sourceSet.manifest.fingerprint || !sameSourceSnapshots(sourceSet, finalSources)) {
      throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Baseline source changed during freeze");
    }
  };
  await replaceFullPackage(taskPackage.target, files, { fs, beforeCommit: verifyBeforeCommit });
  const frozen = await readFullPackage({ root, task, fs });
  return fullOutcome(frozen, { created: true });
}
async function freezeFullBaseline(options = {}) {
  const {
    root,
    task,
    confirmed = false,
    fs = fsPromises7
  } = options;
  if (confirmed !== true) {
    throw new GatedLoopError("CONFIRMATION_REQUIRED", "Baseline freeze requires explicit confirmation");
  }
  if (typeof root !== "string" || root.length === 0) {
    throw new GatedLoopError("BASELINE_ROOT_INVALID", "Project root is required");
  }
  validateTask3(task);
  const target = await assertSafePath(root, path10.join(".ai-dev-loop", task), { fs });
  return withRuntimeDirectoryTransaction(
    target,
    () => freezeFullBaselineLocked({ ...options, fs }),
    { fs }
  );
}

// src/full/prepare.mjs
import * as fsPromises8 from "node:fs/promises";
import path11 from "node:path";
function expectedArtifacts(model, renderedBaseline, manifest) {
  const baselineFingerprint = sha256Bytes(Buffer.from(renderedBaseline, "utf8"));
  const generated = buildBaselineArtifacts(model, { baselineFingerprint });
  return {
    baselineFingerprint,
    baselineText: renderedBaseline,
    manifestText: json4(manifest),
    acceptanceText: json4(generated.acceptance),
    tasksText: json4(generated.tasks)
  };
}
function exactPreparedArtifacts(existing, expected) {
  return existing.bytes["baseline.md"].toString("utf8") === expected.baselineText && existing.bytes["source-manifest.json"].toString("utf8") === expected.manifestText && existing.bytes["acceptance.json"].toString("utf8") === expected.acceptanceText && existing.bytes["tasks.json"].toString("utf8") === expected.tasksText;
}
function changed(error) {
  return error instanceof GatedLoopError ? new GatedLoopError("BASELINE_SOURCE_CHANGED", "Frozen baseline sources changed") : error;
}
function sameBytes2(left, right) {
  return left && right && Buffer.compare(left, right) === 0;
}
async function prepareFullBaselineLocked({
  root,
  task,
  baseline,
  sources = [],
  now = () => /* @__PURE__ */ new Date(),
  beforeCommit,
  fs = fsPromises8
} = {}) {
  const existing = await readFullPackage({ root, task, fs });
  let sourceSet;
  try {
    sourceSet = await readBaselineSources({ root, baseline, sources, fs });
  } catch (error) {
    if (existing.stage === "BASELINE_FROZEN") throw changed(error);
    throw error;
  }
  let model;
  try {
    model = parseFullBaseline(sourceSet.baseline.text, { file: sourceSet.baseline.path });
  } catch (error) {
    if (existing.stage === "BASELINE_FROZEN") throw changed(error);
    throw error;
  }
  model.supportingSourceLines = sourceSet.sources.flatMap((source) => source.lines.map((line) => ({ ...line, section: "Source" })));
  model.allSourceLines = [...model.sourceLines, ...model.supportingSourceLines];
  const renderedBaseline = renderFullBaseline(model);
  const expected = expectedArtifacts(model, renderedBaseline, sourceSet.manifest);
  if (existing.stage) {
    const sameSource = existing.state.sourceFingerprint === sourceSet.manifest.fingerprint && existing.state.baselineFingerprint === expected.baselineFingerprint;
    if (existing.stage === "BASELINE_FROZEN") {
      if (!sameSource || !exactPreparedArtifacts(existing, expected)) throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Frozen baseline cannot be changed");
      validateFrozenPackage(existing, renderFullHandoff(model, task, existing.hostRuntime));
      return fullOutcome(existing);
    }
    if (sameSource) {
      if (!exactPreparedArtifacts(existing, expected)) throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Prepared baseline artifacts changed");
      return fullOutcome(existing);
    }
  }
  const updatedAt = timestamp3(now);
  const decisionBytes = existing.bytes["decision-log.md"] ?? Buffer.from(DEFAULT_DECISION_LOG, "utf8");
  const files = {
    "mode.json": existing.modeBytes,
    "baseline.md": expected.baselineText,
    "acceptance.json": expected.acceptanceText,
    "tasks.json": expected.tasksText,
    "source-manifest.json": expected.manifestText,
    "decision-log.md": decisionBytes
  };
  const hashedNames = ["acceptance.json", "baseline.md", "mode.json", "source-manifest.json", "tasks.json"];
  const hashes = artifactHashes2(files, hashedNames);
  const state = {
    schemaVersion: 1,
    task,
    mode: "full",
    stage: "WAITING_FOR_BASELINE_CONFIRMATION",
    hostRuntime: existing.hostRuntime,
    reviewer: existing.hostRuntime,
    sourceFingerprint: sourceSet.manifest.fingerprint,
    baselineFingerprint: expected.baselineFingerprint,
    inputFingerprint: sha256Bytes(Buffer.from(canonicalJson3({
      task,
      hostRuntime: existing.hostRuntime,
      sourceFingerprint: sourceSet.manifest.fingerprint,
      baselineFingerprint: expected.baselineFingerprint,
      modeSha256: hashes["mode.json"]
    }), "utf8")),
    artifactHashes: hashes,
    updatedAt
  };
  files["state.json"] = json4(state);
  const verifyBeforeCommit = async (staging) => {
    if (beforeCommit) await beforeCommit(staging);
    const current = await readFullPackage({ root, task, fs });
    if (current.stage !== existing.stage || !sameBytes2(current.modeBytes, existing.modeBytes) || existing.stage && (!sameBytes2(current.bytes["state.json"], existing.bytes["state.json"]) || !sameBytes2(current.bytes["decision-log.md"], existing.bytes["decision-log.md"]))) {
      throw new GatedLoopError("BASELINE_SOURCE_CHANGED", "Task package changed during prepare");
    }
    let finalSources;
    try {
      finalSources = await readBaselineSources({ root, baseline, sources, fs });
    } catch {
      throw new GatedLoopError("PATH_FILE_CHANGED", "A baseline source changed during prepare");
    }
    if (finalSources.manifest.fingerprint !== sourceSet.manifest.fingerprint || !sameSourceSnapshots(sourceSet, finalSources)) {
      throw new GatedLoopError("PATH_FILE_CHANGED", "A baseline source changed during prepare");
    }
  };
  await replaceFullPackage(existing.target, files, { fs, beforeCommit: verifyBeforeCommit });
  const prepared = await readFullPackage({ root, task, fs });
  return fullOutcome(prepared, { created: existing.stage === null, updated: existing.stage !== null });
}
async function prepareFullBaseline(options = {}) {
  const { root, task, fs = fsPromises8 } = options;
  if (typeof root !== "string" || root.length === 0) {
    throw new GatedLoopError("BASELINE_ROOT_INVALID", "Project root is required");
  }
  validateTask3(task);
  const target = await assertSafePath(root, path11.join(".ai-dev-loop", task), { fs });
  return withRuntimeDirectoryTransaction(
    target,
    () => prepareFullBaselineLocked({ ...options, fs }),
    { fs }
  );
}

// src/cli/output.mjs
var SENSITIVE = /^(stdout|stderr|env|.*token.*|.*key.*|.*secret.*|.*password.*)$/i;
function redact(value) {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, SENSITIVE.test(key) ? "[REDACTED]" : redact(child)]));
  return value;
}
function renderJson(value) {
  return `${JSON.stringify(redact(value))}
`;
}
function renderError(error) {
  return `ERROR ${error.code}: ${error.message}
`;
}

// src/acceptance/self-check.mjs
import * as fsPromises10 from "node:fs/promises";

// src/config/load-config.mjs
var import_yaml = __toESM(require_dist(), 1);
import { readFile } from "node:fs/promises";
import path12 from "node:path";
var defaults = Object.freeze({
  version: 1,
  runtimeDir: ".ai-dev-loop",
  maxRepairLoops: 3,
  tools: { claude: "claude", codex: "codex", git: "git" },
  protectedPaths: [".ai-dev-loop/**", ".git/**"],
  forbiddenPaths: [".env*", "**/.env*", "**/*production*", "**/*preproduction*"]
});
var KEYS = new Set(Object.keys(defaults));
var TOOL_KEYS = new Set(Object.keys(defaults.tools));
function fail4(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}
function strings2(value, key) {
  if (!Array.isArray(value) || value.some((x) => typeof x !== "string")) fail4("CONFIG_INVALID_TYPE", `${key} must be an array of strings`);
}
function validatePatterns(value, key) {
  strings2(value, key);
  for (const pattern of value) {
    const segments = pattern.split(/[\\/]/);
    const invalid3 = pattern.length === 0 || path12.posix.isAbsolute(pattern) || path12.win32.isAbsolute(pattern) || /^[\\/]{2}/.test(pattern) || segments.includes("..") || pattern.includes(":") || pattern.includes("\0");
    if (invalid3) fail4("INVALID_CONFIG", `${key} contains an unsafe path pattern`, { key, pattern });
  }
}
async function loadConfig(root) {
  let supplied = {};
  try {
    supplied = (0, import_yaml.parse)(await readFile(path12.join(root, ".gated-loop.yml"), "utf8")) ?? {};
  } catch (error) {
    if (error.code !== "ENOENT") fail4("CONFIG_PARSE", "Unable to parse .gated-loop.yml", { cause: error.message });
  }
  if (!supplied || typeof supplied !== "object" || Array.isArray(supplied)) fail4("CONFIG_INVALID_TYPE", "Configuration must be a mapping");
  for (const key of Object.keys(supplied)) if (!KEYS.has(key)) fail4("CONFIG_UNKNOWN_KEY", `Unknown configuration key: ${key}`);
  if (supplied.version !== void 0 && supplied.version !== 1) fail4("CONFIG_VERSION", "Configuration version must be 1");
  if (supplied.runtimeDir !== void 0 && typeof supplied.runtimeDir !== "string") fail4("CONFIG_INVALID_TYPE", "runtimeDir must be a string");
  if (supplied.maxRepairLoops !== void 0 && (!Number.isInteger(supplied.maxRepairLoops) || supplied.maxRepairLoops < 0)) fail4("CONFIG_INVALID_TYPE", "maxRepairLoops must be a non-negative integer");
  if (supplied.tools !== void 0) {
    if (!supplied.tools || typeof supplied.tools !== "object" || Array.isArray(supplied.tools)) fail4("CONFIG_INVALID_TYPE", "tools must be a mapping");
    for (const key of Object.keys(supplied.tools)) if (!TOOL_KEYS.has(key)) fail4("CONFIG_UNKNOWN_KEY", `Unknown tool key: ${key}`);
    for (const [key, value] of Object.entries(supplied.tools)) if (typeof value !== "string" || value.length === 0) fail4("CONFIG_INVALID_TYPE", `tools.${key} must be a non-empty string`);
  }
  if (supplied.protectedPaths !== void 0) validatePatterns(supplied.protectedPaths, "protectedPaths");
  if (supplied.forbiddenPaths !== void 0) validatePatterns(supplied.forbiddenPaths, "forbiddenPaths");
  let runtimeDir;
  try {
    runtimeDir = canonicalRelativePath(supplied.runtimeDir ?? defaults.runtimeDir);
  } catch {
    fail4("CONFIG_PATH_OUTSIDE_ROOT", "runtimeDir must remain within the project");
  }
  return { ...defaults, ...supplied, runtimeDir, tools: { ...defaults.tools, ...supplied.tools }, protectedPaths: supplied.protectedPaths ?? [...defaults.protectedPaths], forbiddenPaths: supplied.forbiddenPaths ?? [...defaults.forbiddenPaths] };
}

// src/core/process.mjs
import { spawn as nodeSpawn } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
var LIMIT = 64 * 1024;
function collector() {
  const chunks = [];
  let size = 0;
  let truncated = false;
  return {
    add(chunk) {
      const bytes = Buffer.from(chunk);
      const remaining = LIMIT - size;
      if (remaining > 0) {
        chunks.push(bytes.subarray(0, remaining));
        size += Math.min(bytes.length, remaining);
      }
      if (bytes.length > remaining) truncated = true;
    },
    text() {
      const decoded = new StringDecoder("utf8").write(Buffer.concat(chunks));
      if (Buffer.byteLength(decoded) <= LIMIT) return decoded;
      let result3 = "";
      let bytes = 0;
      for (const codepoint of decoded) {
        const length = Buffer.byteLength(codepoint);
        if (bytes + length > LIMIT) break;
        result3 += codepoint;
        bytes += length;
      }
      return result3;
    },
    truncated() {
      return truncated;
    }
  };
}
function runProcess(file, args, {
  spawn = nodeSpawn,
  timeoutMs = 0,
  signal,
  cwd,
  env,
  captureOutput = false,
  input
} = {}) {
  if (signal?.aborted) return Promise.reject(new GatedLoopError("PROCESS_ABORTED", `Process aborted: ${file}`));
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer;
    let child;
    let abortRequested = false;
    let killed = false;
    const out = collector();
    const err = collector();
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      fn(value);
    };
    const kill = () => {
      if (child && !killed) {
        killed = true;
        child.kill();
      }
    };
    const abort = () => {
      abortRequested = true;
      kill();
      finish(reject, new GatedLoopError("PROCESS_ABORTED", `Process aborted: ${file}`));
    };
    signal?.addEventListener("abort", abort, { once: true });
    try {
      child = spawn(file, args, { shell: false, cwd, env, windowsHide: true, stdio: [input === void 0 ? "ignore" : "pipe", "pipe", "pipe"] });
    } catch (error) {
      finish(reject, new GatedLoopError("PROCESS_SPAWN_FAILED", `Unable to start process: ${file}`, { details: { cause: error.message, causeCode: error.code } }));
      return;
    }
    if (abortRequested || signal?.aborted) {
      abort();
      return;
    }
    child.stdout?.on("data", (chunk) => out.add(chunk));
    child.stderr?.on("data", (chunk) => err.add(chunk));
    if (input !== void 0) child.stdin?.end(input);
    if (timeoutMs > 0) timer = setTimeout(() => {
      kill();
      finish(reject, new GatedLoopError("PROCESS_TIMEOUT", `Process timed out: ${file}`, { details: { timeoutMs, stdout: out.text(), stderr: err.text() } }));
    }, timeoutMs);
    child.on("error", (error) => finish(reject, new GatedLoopError("PROCESS_SPAWN_FAILED", `Unable to start process: ${file}`, { details: { cause: error.message, causeCode: error.code } })));
    child.on("close", (exitCode, exitSignal) => exitCode === 0 ? finish(resolve, captureOutput ? {
      exitCode,
      signal: exitSignal,
      stdout: out.text(),
      stderr: err.text(),
      stdoutTruncated: out.truncated(),
      stderrTruncated: err.truncated()
    } : { exitCode, signal: exitSignal }) : finish(reject, new GatedLoopError("PROCESS_FAILED", `Process failed: ${file}`, { details: { exitCode, signal: exitSignal, stdout: out.text(), stderr: err.text() } })));
  });
}

// src/acceptance/common.mjs
import * as fsPromises9 from "node:fs/promises";
import path13 from "node:path";
var SHA = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
var SHA256 = /^[a-f0-9]{64}$/;
function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
function json5(value) {
  return `${JSON.stringify(value, null, 2)}
`;
}
function fingerprint(value) {
  return sha256Bytes(Buffer.from(stableJson(value), "utf8"));
}
function normalizeRound(value = "round-01") {
  const text2 = String(value);
  const match = /^(?:round-)?(\d{1,2})$/.exec(text2);
  const number = match ? Number(match[1]) : 0;
  if (!Number.isInteger(number) || number < 1 || number > 99) {
    throw new GatedLoopError("ROUND_INVALID", "Round must be between round-01 and round-99");
  }
  return `round-${String(number).padStart(2, "0")}`;
}
function parseLightBrief(markdown) {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const scope = [];
  const testCommands2 = [];
  let section = "";
  for (const line of lines) {
    const heading = /^## (.+)$/.exec(line);
    if (heading) {
      section = heading[1];
      continue;
    }
    if (section === "Scope" && line.startsWith("- ")) scope.push(canonicalRelativePath(line.slice(2)));
    if (section === "Acceptance" && line.startsWith("- Test command: ")) {
      let value;
      try {
        value = JSON.parse(line.slice("- Test command: ".length));
      } catch {
        throw new GatedLoopError("LIGHT_SOURCE_CHANGED", "Frozen Light test command is invalid");
      }
      const argv = normalizeTestArgv(value);
      if (!argv) throw new GatedLoopError("LIGHT_SOURCE_CHANGED", "Frozen Light test command is unsafe");
      testCommands2.push(argv);
    }
  }
  if (scope.length === 0 || testCommands2.length === 0) {
    throw new GatedLoopError("LIGHT_SOURCE_CHANGED", "Frozen Light scope or test commands are missing");
  }
  return { scope, testCommands: testCommands2 };
}
async function loadFrozenTask({ root, task, fs = fsPromises9 } = {}) {
  const modeBytes = await readSafeRegularFile(root, path13.join(".ai-dev-loop", task, "mode.json"), { fs });
  let mode;
  try {
    mode = JSON.parse(modeBytes.toString("utf8"));
  } catch {
    throw new GatedLoopError("FROZEN_TASK_INVALID", "Frozen task mode is invalid");
  }
  if (mode.mode === "full") {
    const taskPackage = await readFullPackage({ root, task, fs });
    if (taskPackage.stage !== "BASELINE_FROZEN") throw new GatedLoopError("BASELINE_NOT_FROZEN", "Full baseline is not frozen");
    const authority = taskPackage.bytes["baseline.md"].toString("utf8");
    const model = parseFullBaseline(authority);
    return {
      task,
      mode: "full",
      taskPackage,
      authorityName: "baseline.md",
      authority,
      scope: null,
      testCommands: model.testCommands,
      acceptance: taskPackage.acceptance.acceptance,
      tasks: taskPackage.tasks.tasks,
      frozenFingerprint: taskPackage.state.frozenFingerprint
    };
  }
  if (mode.mode === "light") {
    const taskPackage = await readLightPackage({ root, task, fs });
    const authority = taskPackage.bytes["light-brief.md"].toString("utf8");
    const parsed = parseLightBrief(authority);
    return {
      task,
      mode: "light",
      taskPackage,
      authorityName: "light-brief.md",
      authority,
      scope: parsed.scope,
      testCommands: parsed.testCommands,
      acceptance: JSON.parse(taskPackage.bytes["acceptance.json"].toString("utf8")).acceptance,
      tasks: JSON.parse(taskPackage.bytes["tasks.json"].toString("utf8")).tasks,
      frozenFingerprint: taskPackage.state.frozenFingerprint
    };
  }
  throw new GatedLoopError("FROZEN_TASK_INVALID", "Task mode must be full or light");
}
function safePattern(value) {
  if (typeof value !== "string" || value.length === 0 || value.includes("\0") || value.includes(":")) return null;
  let normalized;
  try {
    normalized = canonicalRelativePath(value);
  } catch {
    return null;
  }
  if (!normalized || normalized === "." || normalized.startsWith("../")) return null;
  return normalized;
}
function exactKeys2(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) && stableJson(Object.keys(value).sort()) === stableJson([...keys].sort());
}
function sameStringSet(left, right) {
  if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
  const orderedLeft = [...left].sort();
  const orderedRight = [...right].sort();
  return orderedLeft.every((entry, index) => entry === orderedRight[index]);
}
function validateStringIds(value, allowed, label, { nonempty: nonempty2 = true } = {}) {
  if (!Array.isArray(value) || nonempty2 && value.length === 0 || value.some((entry) => typeof entry !== "string" || !allowed.has(entry)) || new Set(value).size !== value.length) {
    throw new GatedLoopError("SNAPSHOT_INVALID", `Development snapshot contains invalid ${label}`);
  }
  return [...value];
}
function validatePreExisting(value) {
  if (!Array.isArray(value)) throw new GatedLoopError("SNAPSHOT_INVALID", "Development snapshot pre-existing changes must be an array");
  const entries = value.map((entry) => {
    const filePath = safePattern(entry?.path);
    const hashValid = entry?.worktreeSha256 === null || SHA256.test(entry?.worktreeSha256);
    if (!exactKeys2(entry, ["path", "statusCode", "worktreeSha256"]) || !filePath || typeof entry.statusCode !== "string" || entry.statusCode.length !== 2 || !hashValid) {
      throw new GatedLoopError("SNAPSHOT_INVALID", "Development snapshot contains an invalid pre-existing change");
    }
    return { path: filePath, statusCode: entry.statusCode, worktreeSha256: entry.worktreeSha256 };
  });
  if (new Set(entries.map((entry) => entry.path)).size !== entries.length) {
    throw new GatedLoopError("SNAPSHOT_INVALID", "Development snapshot repeats a pre-existing path");
  }
  return entries;
}
function validateAllowedPaths(value, label = "allowed paths") {
  if (!Array.isArray(value) || value.length === 0) {
    throw new GatedLoopError("SNAPSHOT_INVALID", `Development snapshot ${label} must be a non-empty array`);
  }
  const patterns = value.map(safePattern);
  if (patterns.includes(null) || new Set(patterns).size !== patterns.length) {
    throw new GatedLoopError("SNAPSHOT_INVALID", `Development snapshot contains unsafe or duplicate ${label}`);
  }
  if (patterns.some((pattern) => pattern === ".git" || pattern.startsWith(".git/") || pattern === ".ai-dev-loop" || pattern.startsWith(".ai-dev-loop/"))) {
    throw new GatedLoopError("SNAPSHOT_INVALID", `Development snapshot ${label} includes a protected runtime path`);
  }
  return patterns;
}
function validateSnapshot(value, frozen, round) {
  const common = value && typeof value === "object" && !Array.isArray(value) && value.task === frozen.task && value.round === round && value.frozenFingerprint === frozen.frozenFingerprint;
  if (!common) throw new GatedLoopError("SNAPSHOT_INVALID", "Development snapshot does not match the frozen task and round");
  if (value.schemaVersion === 1) {
    if (!exactKeys2(value, ["schemaVersion", "task", "round", "baseCommit", "frozenFingerprint", "allowedPaths", "preExistingChanges"]) || !SHA.test(value.baseCommit)) {
      throw new GatedLoopError("SNAPSHOT_INVALID", "Development snapshot schema v1 is invalid");
    }
    const allowedPaths = validateAllowedPaths(value.allowedPaths);
    const preExistingChanges = validatePreExisting(value.preExistingChanges);
    if (frozen.mode === "light" && allowedPaths.some((pattern) => pattern.includes("*") || !frozen.scope.includes(pattern))) {
      throw new GatedLoopError("SNAPSHOT_INVALID", "Light snapshot paths must exactly match frozen scope files");
    }
    return { ...value, allowedPaths, preExistingChanges };
  }
  if (value.schemaVersion !== 2 || frozen.mode !== "full" || !exactKeys2(value, ["schemaVersion", "task", "round", "frozenFingerprint", "workspaces"]) || !Array.isArray(value.workspaces) || value.workspaces.length < 2) {
    throw new GatedLoopError("SNAPSHOT_INVALID", "Development snapshot schema v2 requires a Full task and at least two workspaces");
  }
  const taskIds = new Set(frozen.tasks.map((entry) => entry.id));
  const workspaces = value.workspaces.map((entry) => {
    const valid = exactKeys2(entry, ["id", "root", "branch", "baseCommit", "taskIds", "allowedPaths", "preExistingChanges"]) && typeof entry.id === "string" && /^[a-z][a-z0-9._-]{0,63}$/.test(entry.id) && typeof entry.root === "string" && path13.isAbsolute(entry.root) && !/[\u0000-\u001f\u007f]/.test(entry.root) && typeof entry.branch === "string" && entry.branch.length > 0 && entry.branch.length <= 256 && !/[\u0000-\u001f\u007f]/.test(entry.branch) && SHA.test(entry.baseCommit);
    if (!valid) throw new GatedLoopError("SNAPSHOT_INVALID", "Development snapshot contains an invalid workspace");
    return {
      ...entry,
      root: path13.resolve(entry.root),
      taskIds: validateStringIds(entry.taskIds, taskIds, "workspace task IDs"),
      allowedPaths: validateAllowedPaths(entry.allowedPaths, "workspace allowed paths"),
      preExistingChanges: validatePreExisting(entry.preExistingChanges)
    };
  });
  const rootKeys = workspaces.map((entry) => process.platform === "win32" ? entry.root.toLowerCase() : entry.root);
  if (new Set(workspaces.map((entry) => entry.id)).size !== workspaces.length || new Set(rootKeys).size !== workspaces.length) {
    throw new GatedLoopError("SNAPSHOT_INVALID", "Development snapshot repeats a workspace ID or root");
  }
  return { ...value, workspaces };
}
async function readSnapshot({ root, task, round, source, frozen, fs = fsPromises9 } = {}) {
  const relative = source ?? path13.join(".ai-dev-loop", task, "rounds", round, "development-snapshot.json");
  let value;
  try {
    value = JSON.parse((await readSafeRegularFile(root, relative, { fs })).toString("utf8"));
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    throw new GatedLoopError("SNAPSHOT_READ", "Unable to read development snapshot");
  }
  return validateSnapshot(value, frozen, round);
}
async function readRoundJson(root, task, round, name, fs) {
  try {
    return JSON.parse((await readSafeRegularFile(root, path13.join(".ai-dev-loop", task, "rounds", round, name), { fs })).toString("utf8"));
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    throw new GatedLoopError("WORKSPACE_GATE_READ", `Unable to read ${name}`);
  }
}
function normalizeTestCommands(value, workspaceRoot) {
  if (!Array.isArray(value) || value.length === 0) {
    throw new GatedLoopError("WORKSPACE_AUTHORIZATION_INVALID", "Every workspace must define at least one test command");
  }
  return value.map((entry) => {
    const argv = normalizeTestArgv(entry?.argv);
    if (!exactKeys2(entry, ["cwd", "argv"]) || typeof entry.cwd !== "string" || !path13.isAbsolute(entry.cwd) || !argv) {
      throw new GatedLoopError("WORKSPACE_AUTHORIZATION_INVALID", "Workspace test command is invalid");
    }
    const cwd = path13.resolve(entry.cwd);
    const relative = path13.relative(workspaceRoot, cwd);
    if (relative === ".." || relative.startsWith(`..${path13.sep}`) || path13.isAbsolute(relative)) {
      throw new GatedLoopError("WORKSPACE_AUTHORIZATION_INVALID", "Workspace test cwd escapes its authorized root");
    }
    return { cwd, argv };
  });
}
function topologicalWorkspacePlan(coverage, workspaces) {
  const byId = new Map(workspaces.map((entry) => [entry.id, entry]));
  const taskToWorkspaces = /* @__PURE__ */ new Map();
  for (const workspace of workspaces) for (const taskId of workspace.taskIds) {
    const ids = taskToWorkspaces.get(taskId) ?? [];
    ids.push(workspace.id);
    taskToWorkspaces.set(taskId, ids);
  }
  const dependencies = new Map(workspaces.map((entry) => [entry.id, /* @__PURE__ */ new Set()]));
  const taskDependencies = new Map(coverage.taskCoverage.map((entry) => [entry.taskId, entry.dependsOn]));
  const visiting = /* @__PURE__ */ new Set();
  const visited = /* @__PURE__ */ new Set();
  const visitTask = (taskId) => {
    if (visiting.has(taskId)) throw new GatedLoopError("WORKSPACE_DEPENDENCY_CYCLE", "Workspace task dependency graph contains a cycle");
    if (visited.has(taskId)) return;
    visiting.add(taskId);
    for (const dependency of taskDependencies.get(taskId) ?? []) visitTask(dependency);
    visiting.delete(taskId);
    visited.add(taskId);
  };
  for (const taskId of taskDependencies.keys()) visitTask(taskId);
  for (const entry of coverage.taskCoverage) {
    for (const dependency of entry.dependsOn) {
      for (const target of entry.workspaceIds) for (const source of taskToWorkspaces.get(dependency) ?? []) {
        if (source !== target) dependencies.get(target).add(source);
      }
    }
  }
  const waves = /* @__PURE__ */ new Map();
  const resolving = /* @__PURE__ */ new Set();
  const wave = (id) => {
    if (waves.has(id)) return waves.get(id);
    if (resolving.has(id)) throw new GatedLoopError("WORKSPACE_DEPENDENCY_CYCLE", "Workspace dependency graph contains a cycle");
    resolving.add(id);
    const value = 1 + Math.max(0, ...[...dependencies.get(id)].map(wave));
    resolving.delete(id);
    waves.set(id, value);
    return value;
  };
  for (const id of byId.keys()) wave(id);
  return workspaces.map((entry) => ({
    ...entry,
    dependsOnWorkspaceIds: [...dependencies.get(entry.id)].sort(),
    wave: waves.get(entry.id)
  })).sort((left, right) => left.wave - right.wave || left.id.localeCompare(right.id));
}
async function loadWorkspacePlan({ root, task, round, snapshot, frozen, fs = fsPromises9 } = {}) {
  if (snapshot.schemaVersion === 1) return [{
    id: "coordinator",
    root: path13.resolve(root),
    branch: null,
    baseCommit: snapshot.baseCommit,
    taskIds: frozen.tasks.map((entry) => entry.id),
    allowedPaths: snapshot.allowedPaths,
    preExistingChanges: snapshot.preExistingChanges,
    testCommands: frozen.testCommands.map((argv) => ({ cwd: path13.resolve(root), argv })),
    dependsOnWorkspaceIds: [],
    wave: 1,
    coordinator: true
  }];
  const authorization = await readRoundJson(root, task, round, "workspace-authorization.json", fs);
  const coverage = await readRoundJson(root, task, round, "workspace-coverage.json", fs);
  const authorizationValid = exactKeys2(authorization, ["schemaVersion", "task", "round", "coordinatorWorkspaceId", "status", "confirmedBy", "workspaces"]) && authorization.schemaVersion === 1 && authorization.task === task && authorization.round === round && authorization.status === "CONFIRMED" && authorization.confirmedBy === "user" && typeof authorization.coordinatorWorkspaceId === "string" && Array.isArray(authorization.workspaces);
  if (!authorizationValid) throw new GatedLoopError("WORKSPACE_AUTHORIZATION_INVALID", "Workspace authorization is invalid or not user-confirmed");
  const frozenTaskIds = new Set(frozen.tasks.map((entry) => entry.id));
  const snapshotById = new Map(snapshot.workspaces.map((entry) => [entry.id, entry]));
  const workspaces = authorization.workspaces.map((entry) => {
    const snapshotEntry = snapshotById.get(entry?.id);
    const rootPath = typeof entry?.root === "string" && path13.isAbsolute(entry.root) ? path13.resolve(entry.root) : null;
    const valid = exactKeys2(entry, ["id", "root", "access", "taskIds", "allowedPaths", "testCommands"]) && snapshotEntry && rootPath && entry.access === "read-write";
    if (!valid) throw new GatedLoopError("WORKSPACE_AUTHORIZATION_INVALID", "Workspace authorization entry is invalid");
    const taskIds = validateStringIds(entry.taskIds, frozenTaskIds, "authorized task IDs");
    const allowedPaths = validateAllowedPaths(entry.allowedPaths, "authorized allowed paths");
    if (!sameAbsolutePath(rootPath, snapshotEntry.root) || !sameStringSet(taskIds, snapshotEntry.taskIds) || !sameStringSet(allowedPaths, snapshotEntry.allowedPaths)) {
      throw new GatedLoopError("WORKSPACE_AUTHORIZATION_MISMATCH", "Workspace authorization does not match the development snapshot");
    }
    return { ...snapshotEntry, testCommands: normalizeTestCommands(entry.testCommands, rootPath) };
  });
  if (workspaces.length !== snapshot.workspaces.length || new Set(workspaces.map((entry) => entry.id)).size !== workspaces.length || !snapshotById.has(authorization.coordinatorWorkspaceId)) {
    throw new GatedLoopError("WORKSPACE_AUTHORIZATION_MISMATCH", "Workspace authorization does not cover every snapshot workspace");
  }
  const coordinator = workspaces.find((entry) => entry.id === authorization.coordinatorWorkspaceId);
  if (!sameAbsolutePath(coordinator.root, root)) {
    throw new GatedLoopError("WORKSPACE_AUTHORIZATION_MISMATCH", "Coordinator workspace root does not match the CLI project root");
  }
  const authorizedCommands = workspaces.flatMap((entry) => entry.testCommands.map(({ argv }) => JSON.stringify(argv))).sort();
  const frozenCommands = frozen.testCommands.map((argv) => JSON.stringify(argv)).sort();
  if (!sameStringSet(authorizedCommands, frozenCommands)) {
    throw new GatedLoopError("WORKSPACE_TEST_COMMAND_MISMATCH", "Workspace test commands must exactly partition the frozen test commands");
  }
  const coverageValid = exactKeys2(coverage, ["schemaVersion", "task", "round", "status", "taskCoverage", "missing"]) && coverage.schemaVersion === 1 && coverage.task === task && coverage.round === round && coverage.status === "PASS" && Array.isArray(coverage.taskCoverage) && Array.isArray(coverage.missing) && coverage.missing.length === 0;
  if (!coverageValid) throw new GatedLoopError("WORKSPACE_COVERAGE_INVALID", "Workspace coverage is not PASS");
  const workspaceIds = new Set(workspaces.map((entry) => entry.id));
  const taskCoverage = coverage.taskCoverage.map((entry) => {
    const valid = exactKeys2(entry, ["taskId", "workspaceIds", "dependsOn", "status"]) && frozenTaskIds.has(entry?.taskId) && entry.status === "COVERED";
    if (!valid) throw new GatedLoopError("WORKSPACE_COVERAGE_INVALID", "Workspace task coverage entry is invalid");
    const coveredIds = validateStringIds(entry.workspaceIds, workspaceIds, "covered workspace IDs");
    const dependsOn = validateStringIds(entry.dependsOn, frozenTaskIds, "task dependencies", { nonempty: false });
    if (dependsOn.includes(entry.taskId)) throw new GatedLoopError("WORKSPACE_DEPENDENCY_CYCLE", "A task cannot depend on itself");
    const authorizedIds = workspaces.filter((workspace) => workspace.taskIds.includes(entry.taskId)).map((workspace) => workspace.id);
    if (!sameStringSet(coveredIds, authorizedIds)) {
      throw new GatedLoopError("WORKSPACE_COVERAGE_INVALID", "Task coverage does not match workspace authorization");
    }
    return { ...entry, workspaceIds: coveredIds, dependsOn };
  });
  if (taskCoverage.length !== frozenTaskIds.size || new Set(taskCoverage.map((entry) => entry.taskId)).size !== frozenTaskIds.size) {
    throw new GatedLoopError("WORKSPACE_COVERAGE_INVALID", "Workspace coverage must include every frozen task exactly once");
  }
  for (const workspace of workspaces) {
    await assertSafePath(workspace.root, workspace.root, { fs });
    const stat = await fs.lstat(workspace.root);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new GatedLoopError("WORKSPACE_ROOT_INVALID", "Workspace root must be a real directory");
    for (const command of workspace.testCommands) {
      await assertSafePath(workspace.root, command.cwd, { fs });
      const cwdStat = await fs.lstat(command.cwd);
      if (!cwdStat.isDirectory() || cwdStat.isSymbolicLink()) throw new GatedLoopError("WORKSPACE_TEST_CWD_INVALID", "Workspace test cwd must be a real directory");
    }
  }
  const planned = topologicalWorkspacePlan({ ...coverage, taskCoverage }, workspaces);
  return planned.map((entry) => ({ ...entry, coordinator: entry.id === authorization.coordinatorWorkspaceId }));
}
function globRegex(pattern) {
  let source = "^";
  for (let index = 0; index < pattern.length; index++) {
    const character = pattern[index];
    if (character === "*" && pattern[index + 1] === "*") {
      source += ".*";
      index++;
    } else if (character === "*") source += "[^/]*";
    else if (character === "?") source += "[^/]";
    else source += character.replace(/[|\\{}()[\]^$+?.]/g, "\\$&");
  }
  return new RegExp(`${source}$`);
}
function matchesAny(filePath, patterns) {
  return patterns.some((pattern) => globRegex(pattern).test(filePath));
}
function parseGitStatus(output) {
  const tokens = output.split("\0");
  const entries = [];
  for (let index = 0; index < tokens.length; index++) {
    const token = tokens[index];
    if (!token) continue;
    if (token.length < 4 || token[2] !== " ") throw new GatedLoopError("GIT_STATUS_INVALID", "Git status output is malformed");
    const statusCode = token.slice(0, 2);
    const filePath = canonicalRelativePath(token.slice(3));
    entries.push({ path: filePath, statusCode });
    if (/[RC]/.test(statusCode)) {
      const original = tokens[++index];
      if (!original) throw new GatedLoopError("GIT_STATUS_INVALID", "Git rename status is incomplete");
      entries.push({ path: canonicalRelativePath(original), statusCode: "D " });
    }
  }
  return entries.sort((left, right) => left.path.localeCompare(right.path));
}
async function gitOutput(root, git, args, { runProcessImpl = runProcess, timeoutMs = 3e4 } = {}) {
  return runProcessImpl(git, args, { cwd: root, timeoutMs, captureOutput: true });
}
async function currentStatus({ root, git = "git", runProcessImpl, timeoutMs } = {}) {
  const topLevel = (await gitOutput(root, git, ["rev-parse", "--show-toplevel"], { runProcessImpl, timeoutMs })).stdout.trim();
  if (!topLevel || !path13.isAbsolute(topLevel) || !sameAbsolutePath(topLevel, root)) {
    throw new GatedLoopError("GIT_ROOT_MISMATCH", "Workspace root must be the Git worktree root");
  }
  const head = (await gitOutput(root, git, ["rev-parse", "HEAD"], { runProcessImpl, timeoutMs })).stdout.trim();
  if (!SHA.test(head)) throw new GatedLoopError("GIT_HEAD_INVALID", "Git HEAD is invalid");
  const branch = (await gitOutput(root, git, ["rev-parse", "--abbrev-ref", "HEAD"], { runProcessImpl, timeoutMs })).stdout.trim();
  if (!branch || /[\u0000-\u001f\u007f]/.test(branch)) {
    throw new GatedLoopError("GIT_BRANCH_INVALID", "Git branch is invalid");
  }
  const statusResult = await gitOutput(root, git, ["status", "--porcelain=v1", "-z", "--untracked-files=all"], { runProcessImpl, timeoutMs });
  if (statusResult.stdoutTruncated) throw new GatedLoopError("GIT_STATUS_TRUNCATED", "Git status is too large to attribute safely");
  return { topLevel: path13.resolve(topLevel), head, branch, entries: parseGitStatus(statusResult.stdout) };
}
async function worktreeHash(root, filePath, { fs = fsPromises9 } = {}) {
  try {
    return sha256Bytes(await readSafeRegularFile(root, filePath, { fs }));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    if (error instanceof GatedLoopError) throw error;
    throw new GatedLoopError("WORKTREE_READ_FAILED", `Unable to read changed file: ${filePath}`);
  }
}
async function enrichStatus(root, entries, { fs = fsPromises9, skipPatterns = [], skipPaths = [] } = {}) {
  const skipped = new Set(skipPaths);
  return Promise.all(entries.map(async (entry) => ({
    ...entry,
    worktreeSha256: skipped.has(entry.path) || matchesAny(entry.path, skipPatterns) ? "[NOT_READ]" : await worktreeHash(root, entry.path, { fs })
  })));
}
function attributeChanges(current, snapshot) {
  const previous = new Map(snapshot.preExistingChanges.map((entry) => [entry.path, entry]));
  const currentByPath = new Map(current.map((entry) => [entry.path, entry]));
  const ambiguous = [];
  const unchangedPreExisting = [];
  for (const entry of snapshot.preExistingChanges) {
    const now = currentByPath.get(entry.path);
    if (now && now.statusCode === entry.statusCode && now.worktreeSha256 === entry.worktreeSha256) unchangedPreExisting.push(entry.path);
    else ambiguous.push(entry.path);
  }
  const changed2 = current.filter((entry) => !previous.has(entry.path));
  return { changed: changed2, ambiguous, unchangedPreExisting };
}
function testCounts(output) {
  const value = { passed: null, failed: null, errors: null, skipped: null };
  const tap = { passed: /# pass\s+(\d+)/i, failed: /# fail\s+(\d+)/i, skipped: /# skipped\s+(\d+)/i };
  for (const [key, pattern] of Object.entries(tap)) {
    const match = pattern.exec(output);
    if (match) value[key] = Number(match[1]);
  }
  const pytest = /(\d+) passed/.exec(output);
  if (pytest) value.passed = Number(pytest[1]);
  const pytestFailed = /(\d+) failed/.exec(output);
  if (pytestFailed) value.failed = Number(pytestFailed[1]);
  const pytestErrors = /(\d+) errors?/.exec(output);
  if (pytestErrors) value.errors = Number(pytestErrors[1]);
  const pytestSkipped = /(\d+) skipped/.exec(output);
  if (pytestSkipped) value.skipped = Number(pytestSkipped[1]);
  return value;
}
async function buildDiffBundle({ root, git = "git", changed: changed2, runProcessImpl, timeoutMs = 3e4, fs = fsPromises9 } = {}) {
  const paths = [...new Set(changed2.map((entry) => entry.path))].sort();
  const tracked = changed2.filter((entry) => entry.statusCode !== "??").map((entry) => entry.path);
  let diff = "";
  let truncated = false;
  if (tracked.length > 0) {
    const result3 = await gitOutput(root, git, ["diff", "--no-ext-diff", "--unified=40", "HEAD", "--", ...tracked], { runProcessImpl, timeoutMs });
    diff = result3.stdout;
    truncated = result3.stdoutTruncated;
  }
  const untracked = [];
  let untrackedBytes = 0;
  for (const entry of changed2.filter((item) => item.statusCode === "??")) {
    const bytes = await readSafeRegularFile(root, entry.path, { fs });
    untrackedBytes += bytes.length;
    if (untrackedBytes > 64 * 1024) {
      truncated = true;
      break;
    }
    untracked.push({ path: entry.path, content: bytes.toString("utf8") });
  }
  const text2 = [
    "# Changed paths",
    ...paths.map((filePath) => `- ${filePath}`),
    "",
    "# Tracked diff",
    diff,
    "",
    "# Untracked files",
    ...untracked.flatMap((entry) => [`## ${entry.path}`, entry.content, ""])
  ].join("\n");
  return { paths, text: text2, truncated, sha256: sha256Bytes(Buffer.from(text2, "utf8")) };
}
function sameAbsolutePath(left, right) {
  const normalizedLeft = path13.resolve(left);
  const normalizedRight = path13.resolve(right);
  return process.platform === "win32" ? normalizedLeft.toLowerCase() === normalizedRight.toLowerCase() : normalizedLeft === normalizedRight;
}
async function inspectWorkspace({
  coordinatorRoot,
  task,
  workspace,
  git = "git",
  protectedPaths = [],
  forbiddenPaths = [],
  isPolicyForbidden = () => false,
  runProcessImpl,
  timeoutMs = 3e4,
  fs = fsPromises9
} = {}) {
  const repository = await currentStatus({ root: workspace.root, git, runProcessImpl, timeoutMs });
  const runtimePrefix = `.ai-dev-loop/${task}/`;
  const relevant = repository.entries.filter((entry) => !(workspace.coordinator && sameAbsolutePath(workspace.root, coordinatorRoot) && entry.path.startsWith(runtimePrefix)));
  const protectedChanged = relevant.filter((entry) => matchesAny(entry.path, protectedPaths));
  const forbiddenChanged = relevant.filter((entry) => matchesAny(entry.path, forbiddenPaths) || isPolicyForbidden(entry.path));
  const enriched = await enrichStatus(workspace.root, relevant, {
    fs,
    skipPatterns: forbiddenPaths,
    skipPaths: forbiddenChanged.map((entry) => entry.path)
  });
  const attributed = attributeChanges(enriched, workspace);
  const outOfScope = attributed.changed.filter((entry) => !matchesAny(entry.path, workspace.allowedPaths));
  const forbiddenSet = new Set(forbiddenChanged.map((entry) => entry.path));
  const safeChanged = attributed.changed.filter((entry) => !forbiddenSet.has(entry.path));
  const diffBundle = await buildDiffBundle({
    root: workspace.root,
    git,
    changed: safeChanged,
    runProcessImpl,
    timeoutMs,
    fs
  });
  return {
    workspace,
    repository,
    relevant,
    protectedChanged,
    forbiddenChanged,
    outOfScope,
    changed: attributed.changed,
    ambiguous: attributed.ambiguous,
    unchangedPreExisting: attributed.unchangedPreExisting,
    diffBundle
  };
}
function aggregateDiffBundles(inspections) {
  const ordered = [...inspections].sort((left, right) => left.workspace.id.localeCompare(right.workspace.id));
  const text2 = ordered.map((entry) => `# Workspace ${entry.workspace.id}
${entry.diffBundle.text}`).join("\n\n");
  return {
    text: text2,
    truncated: ordered.some((entry) => entry.diffBundle.truncated),
    sha256: sha256Bytes(Buffer.from(text2, "utf8")),
    workspaces: ordered.map((entry) => ({ workspaceId: entry.workspace.id, sha256: entry.diffBundle.sha256 }))
  };
}
async function roundDirectory({ root, task, round, fs = fsPromises9 } = {}) {
  const target = await assertSafePath(root, path13.join(".ai-dev-loop", task, "rounds", round), { fs });
  await fs.mkdir(target, { recursive: true });
  await assertSafePath(root, target, { fs });
  const stat = await fs.lstat(target);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new GatedLoopError("ROUND_DIRECTORY_INVALID", "Round directory is invalid");
  return target;
}
async function writeRoundFile(directory, name, content2, { fs = fsPromises9 } = {}) {
  await atomicWriteFile(path13.join(directory, name), content2, { fs });
  return path13.join(directory, name);
}

// src/acceptance/self-check.mjs
function iso(now) {
  const value = typeof now === "function" ? now() : /* @__PURE__ */ new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) throw new GatedLoopError("SELF_CHECK_TIMESTAMP_INVALID", "Self-check timestamp is invalid");
  return date.toISOString();
}
function display(value) {
  return value === null ? "\u672A\u89E3\u6790" : String(value);
}
function policyForbidden(filePath) {
  try {
    normalizeBaselineInputPath(filePath);
    return false;
  } catch {
    return true;
  }
}
function renderSelfCheck(evidence) {
  const testRows = evidence.tests.length === 0 ? ["| \u672A\u8FD0\u884C | - | - | - | - | - | BLOCKED |"] : evidence.tests.map((entry) => `| \`${JSON.stringify(entry.argv)}\` | ${entry.exitCode ?? "-"} | ${display(entry.counts.passed)} | ${display(entry.counts.failed)} | ${display(entry.counts.errors)} | ${display(entry.counts.skipped)} | ${entry.status} |`);
  const changes = evidence.changedFiles.length > 0 ? evidence.changedFiles.map((entry) => `- ${entry}`).join("\n") : "- \u65E0";
  const preExisting = evidence.preExistingUnchanged.length > 0 ? evidence.preExistingUnchanged.map((entry) => `- ${entry}`).join("\n") : "- \u65E0";
  const blockers = evidence.blockers.length > 0 ? evidence.blockers.map((entry) => `- ${entry}`).join("\n") : "- \u65E0";
  const review = evidence.humanReviewReasons.length > 0 ? evidence.humanReviewReasons.map((entry) => `- ${entry}`).join("\n") : "- \u65E0";
  return `# ${evidence.task} ${evidence.round} \u673A\u68B0\u81EA\u68C0\u62A5\u544A

## \u7ED3\u8BBA
${evidence.status}

## \u51BB\u7ED3\u5B8C\u6574\u6027
- \u57FA\u7EBF\u6307\u7EB9\uFF1A${evidence.checks.frozenFingerprint ? "\u5339\u914D" : "\u4E0D\u5339\u914D"}
- \u5F00\u53D1\u524D commit\uFF1A${evidence.baseCommit ?? "\u672A\u77E5"}
- \u5F53\u524D commit\uFF1A${evidence.headCommit ?? "\u672A\u77E5"}

## \u6539\u52A8\u5F52\u5C5E\u4E0E\u8303\u56F4
- \u672C\u8F6E\u771F\u5B9E\u6539\u52A8\uFF1A
${changes}
- \u672A\u53D8\u5316\u7684\u5F00\u53D1\u524D\u5DF2\u6709\u6539\u52A8\uFF1A
${preExisting}
- \u5F52\u5C5E\u4E0D\u660E\u786E\uFF1A${evidence.ambiguousPaths.length > 0 ? evidence.ambiguousPaths.join("\u3001") : "\u65E0"}
- \u8303\u56F4\u68C0\u67E5\uFF1A${evidence.checks.scope ? "\u901A\u8FC7" : "\u5931\u8D25"}

## \u4FDD\u62A4\u9879\u68C0\u67E5
- \u51BB\u7ED3\u4EA7\u7269\uFF1A${evidence.checks.frozenFingerprint ? "\u901A\u8FC7" : "\u5931\u8D25"}
- \u53D7\u4FDD\u62A4\u8DEF\u5F84\uFF1A${evidence.checks.protectedPaths ? "\u901A\u8FC7" : "\u5931\u8D25"}
- \u654F\u611F\u6587\u4EF6\uFF1A${evidence.checks.forbiddenPaths ? "\u672A\u8BFB\u53D6\u4E14\u672A\u53D1\u73B0\u6539\u52A8" : "\u53D1\u73B0\u6539\u52A8\uFF0C\u5185\u5BB9\u672A\u8BFB\u53D6"}

## \u6D4B\u8BD5\u8BC1\u636E
| argv | exitCode | passed | failed | errors | skipped | \u7ED3\u8BBA |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
${testRows.join("\n")}

## \u786E\u5B9A\u6027\u963B\u65AD\u9879
${blockers}

## \u9700\u8981\u4EBA\u5DE5\u5224\u65AD
${review}
`;
}
function renderMultiSelfCheck(evidence) {
  const workspaceRows = evidence.workspaces.length === 0 ? ["| \u672A\u89E3\u6790 | - | - | - | - | - | BLOCKED |"] : evidence.workspaces.map((entry) => `| ${entry.workspaceId} | ${entry.wave} | ${entry.dependsOnWorkspaceIds.join("\u3001") || "-"} | ${entry.baseCommit} | ${entry.headCommit ?? "-"} | ${entry.changedFiles.length} | ${entry.status} |`);
  const testRows = evidence.tests.length === 0 ? ["| \u672A\u8FD0\u884C | - | - | - | - | - | - | - | BLOCKED |"] : evidence.tests.map((entry) => `| ${entry.workspaceId} | ${entry.cwd} | ${JSON.stringify(entry.argv)} | ${entry.exitCode ?? "-"} | ${display(entry.counts.passed)} | ${display(entry.counts.failed)} | ${display(entry.counts.errors)} | ${display(entry.counts.skipped)} | ${entry.status} |`);
  const changes = evidence.changedFiles.length > 0 ? evidence.changedFiles.map((entry) => `- ${entry.workspaceId}:${entry.path}`).join("\n") : "- \u65E0";
  const blockers = evidence.blockers.length > 0 ? evidence.blockers.map((entry) => `- ${entry}`).join("\n") : "- \u65E0";
  const review = evidence.humanReviewReasons.length > 0 ? evidence.humanReviewReasons.map((entry) => `- ${entry}`).join("\n") : "- \u65E0";
  return `# ${evidence.task} ${evidence.round} \u591A\u5DE5\u4F5C\u533A\u673A\u68B0\u81EA\u68C0\u62A5\u544A

## \u7ED3\u8BBA
${evidence.status}

## \u5DE5\u4F5C\u533A\u8986\u76D6\u4E0E\u4F9D\u8D56
- \u5FEB\u7167\uFF1Aschema v2
- \u51BB\u7ED3\u6307\u7EB9\uFF1A${evidence.checks.frozenFingerprint ? "\u5339\u914D" : "\u4E0D\u5339\u914D"}
- \u5DE5\u4F5C\u533A\u8986\u76D6\uFF1A${evidence.checks.workspaceCoverage ? "\u901A\u8FC7" : "\u5931\u8D25"}
- \u4F9D\u8D56\u56FE\uFF1A${evidence.checks.dependencyGraph ? "\u65E0\u73AF\u5E76\u5DF2\u6309\u6CE2\u6B21\u6267\u884C" : "\u5931\u8D25"}

| \u5DE5\u4F5C\u533A | \u6CE2\u6B21 | \u524D\u7F6E\u5DE5\u4F5C\u533A | \u5F00\u53D1\u524D commit | \u5F53\u524D commit | \u6539\u52A8\u6570 | \u7ED3\u8BBA |
| --- | ---: | --- | --- | --- | ---: | --- |
${workspaceRows.join("\n")}

## \u805A\u5408\u6539\u52A8
${changes}

## \u6D4B\u8BD5\u8BC1\u636E
| \u5DE5\u4F5C\u533A | cwd | argv | exitCode | passed | failed | errors | skipped | \u7ED3\u8BBA |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
${testRows.join("\n")}

## \u786E\u5B9A\u6027\u963B\u65AD\u9879
${blockers}

## \u9700\u8981\u4EBA\u5DE5\u5224\u65AD
${review}
`;
}
async function executeTests(frozen, { root, runProcessImpl, timeoutMs }) {
  const results = [];
  for (const argv of frozen.testCommands) {
    try {
      const result3 = await runProcessImpl(argv[0], argv.slice(1), { cwd: root, timeoutMs, captureOutput: true });
      const output = `${result3.stdout ?? ""}
${result3.stderr ?? ""}`;
      results.push({
        argv,
        exitCode: result3.exitCode,
        signal: result3.signal,
        counts: testCounts(output),
        status: "PASS",
        outputTruncated: Boolean(result3.stdoutTruncated || result3.stderrTruncated)
      });
    } catch (error) {
      const details = error instanceof GatedLoopError ? error.details : {};
      const output = `${details.stdout ?? ""}
${details.stderr ?? ""}`;
      results.push({
        argv,
        exitCode: details.exitCode ?? null,
        signal: details.signal ?? null,
        counts: testCounts(output),
        status: "FAIL",
        errorCode: error.code ?? "PROCESS_FAILED",
        outputTruncated: false
      });
    }
  }
  return results;
}
async function executeWorkspaceTests(workspace, { runProcessImpl, timeoutMs }) {
  const results = [];
  for (const command of workspace.testCommands) {
    try {
      const result3 = await runProcessImpl(command.argv[0], command.argv.slice(1), {
        cwd: command.cwd,
        timeoutMs,
        captureOutput: true
      });
      const output = `${result3.stdout ?? ""}
${result3.stderr ?? ""}`;
      results.push({
        workspaceId: workspace.id,
        wave: workspace.wave,
        cwd: command.cwd,
        argv: command.argv,
        exitCode: result3.exitCode,
        signal: result3.signal,
        counts: testCounts(output),
        status: "PASS",
        outputTruncated: Boolean(result3.stdoutTruncated || result3.stderrTruncated)
      });
    } catch (error) {
      const details = error instanceof GatedLoopError ? error.details : {};
      const output = `${details.stdout ?? ""}
${details.stderr ?? ""}`;
      results.push({
        workspaceId: workspace.id,
        wave: workspace.wave,
        cwd: command.cwd,
        argv: command.argv,
        exitCode: details.exitCode ?? null,
        signal: details.signal ?? null,
        counts: testCounts(output),
        status: "FAIL",
        errorCode: error.code ?? "PROCESS_FAILED",
        outputTruncated: false
      });
    }
  }
  return results;
}
function blockedWorkspaceTests(workspace, dependencyIds) {
  return workspace.testCommands.map((command) => ({
    workspaceId: workspace.id,
    wave: workspace.wave,
    cwd: command.cwd,
    argv: command.argv,
    exitCode: null,
    signal: null,
    counts: testCounts(""),
    status: "BLOCKED",
    errorCode: "WORKSPACE_DEPENDENCY_BLOCKED",
    blockedBy: dependencyIds,
    outputTruncated: false
  }));
}
async function runMultiWorkspaceSelfCheck({
  root,
  task,
  round,
  frozen,
  snapshot,
  config,
  directory,
  fs,
  runProcessImpl,
  timeoutMs,
  now
}) {
  const blockers = [];
  const humanReviewReasons = [];
  let plan = [];
  let coverageValid = false;
  let dependencyGraph = false;
  try {
    plan = await loadWorkspacePlan({ root, task, round, snapshot, frozen, fs });
    coverageValid = true;
    dependencyGraph = true;
  } catch (error) {
    humanReviewReasons.push(`${error.code ?? "WORKSPACE_GATE_INVALID"}\uFF1A${error.message}`);
  }
  const inspections = [];
  const workspaceState = /* @__PURE__ */ new Map();
  for (const workspace of plan) {
    const workspaceBlockers = [];
    const workspaceReview = [];
    let inspection = null;
    try {
      inspection = await inspectWorkspace({
        coordinatorRoot: root,
        task,
        workspace,
        git: config.tools.git,
        protectedPaths: config.protectedPaths,
        forbiddenPaths: config.forbiddenPaths,
        isPolicyForbidden: policyForbidden,
        runProcessImpl,
        timeoutMs,
        fs
      });
      inspections.push(inspection);
      if (inspection.repository.head !== workspace.baseCommit) workspaceBlockers.push("\u5F53\u524D HEAD \u4E0E\u5F00\u53D1\u524D\u5FEB\u7167 commit \u4E0D\u4E00\u81F4");
      if (inspection.repository.branch !== workspace.branch) workspaceBlockers.push(`\u5F53\u524D\u5206\u652F\u4E0E\u5FEB\u7167\u4E0D\u4E00\u81F4\uFF1A${inspection.repository.branch}`);
      if (inspection.protectedChanged.length > 0) workspaceBlockers.push(`\u53D7\u4FDD\u62A4\u8DEF\u5F84\u53D1\u751F\u6539\u52A8\uFF1A${inspection.protectedChanged.map((entry) => entry.path).join("\u3001")}`);
      if (inspection.forbiddenChanged.length > 0) workspaceBlockers.push(`\u654F\u611F\u8DEF\u5F84\u53D1\u751F\u6539\u52A8\uFF08\u672A\u8BFB\u53D6\u5185\u5BB9\uFF09\uFF1A${inspection.forbiddenChanged.map((entry) => entry.path).join("\u3001")}`);
      if (inspection.outOfScope.length > 0) workspaceBlockers.push(`\u672C\u8F6E\u6539\u52A8\u8D85\u51FA\u5141\u8BB8\u8303\u56F4\uFF1A${inspection.outOfScope.map((entry) => entry.path).join("\u3001")}`);
      if (inspection.changed.length === 0) workspaceBlockers.push("\u672A\u68C0\u6D4B\u5230\u53EF\u5F52\u5C5E\u4E8E\u672C\u8F6E\u7684\u4ED3\u5E93\u6539\u52A8");
      if (inspection.ambiguous.length > 0) workspaceReview.push(`\u5F00\u53D1\u524D\u5DF2\u6709\u6539\u52A8\u5728\u672C\u8F6E\u53D1\u751F\u53D8\u5316\u6216\u6D88\u5931\uFF1A${inspection.ambiguous.join("\u3001")}`);
      if (inspection.diffBundle.truncated) workspaceReview.push("\u6700\u7EC8 diff \u8D85\u8FC7\u5B89\u5168\u4E0A\u4E0B\u6587\u4E0A\u9650\uFF0C\u65E0\u6CD5\u5B8C\u6574\u9A8C\u6536");
    } catch (error) {
      workspaceReview.push(`${error.code ?? "GIT_EVIDENCE_FAILED"}\uFF1A\u65E0\u6CD5\u53D6\u5F97\u5B8C\u6574 Git \u8BC1\u636E`);
    }
    for (const message of workspaceBlockers) blockers.push(`[${workspace.id}] ${message}`);
    for (const message of workspaceReview) humanReviewReasons.push(`[${workspace.id}] ${message}`);
    workspaceState.set(workspace.id, {
      workspace,
      inspection,
      blockers: workspaceBlockers,
      humanReviewReasons: workspaceReview,
      tests: [],
      status: workspaceBlockers.length > 0 ? "FAIL" : workspaceReview.length > 0 ? "NEED_HUMAN_REVIEW" : "PENDING"
    });
  }
  const tests = [];
  for (const workspace of plan) {
    const state = workspaceState.get(workspace.id);
    const failedDependencies = workspace.dependsOnWorkspaceIds.filter((id) => workspaceState.get(id)?.status !== "PASS");
    if (failedDependencies.length > 0) {
      state.tests = blockedWorkspaceTests(workspace, failedDependencies);
      state.blockers.push(`\u524D\u7F6E\u5DE5\u4F5C\u533A\u95E8\u7981\u672A\u901A\u8FC7\uFF1A${failedDependencies.join("\u3001")}`);
      blockers.push(`[${workspace.id}] \u524D\u7F6E\u5DE5\u4F5C\u533A\u95E8\u7981\u672A\u901A\u8FC7\uFF1A${failedDependencies.join("\u3001")}`);
      state.status = "FAIL";
    } else {
      state.tests = await executeWorkspaceTests(workspace, { runProcessImpl, timeoutMs });
      for (const result3 of state.tests) {
        if (result3.status !== "PASS") {
          state.blockers.push(`\u6D4B\u8BD5\u5931\u8D25\uFF1A${JSON.stringify(result3.argv)}`);
          blockers.push(`[${workspace.id}] \u6D4B\u8BD5\u5931\u8D25\uFF1A${JSON.stringify(result3.argv)}`);
        }
        if (result3.outputTruncated) {
          state.humanReviewReasons.push(`\u6D4B\u8BD5\u8F93\u51FA\u88AB\u622A\u65AD\uFF1A${JSON.stringify(result3.argv)}`);
          humanReviewReasons.push(`[${workspace.id}] \u6D4B\u8BD5\u8F93\u51FA\u88AB\u622A\u65AD\uFF1A${JSON.stringify(result3.argv)}`);
        }
      }
      state.status = state.blockers.length > 0 ? "FAIL" : state.humanReviewReasons.length > 0 ? "NEED_HUMAN_REVIEW" : "PASS";
    }
    tests.push(...state.tests);
  }
  const bundle = inspections.length === plan.length ? aggregateDiffBundles(inspections) : null;
  if (bundle?.truncated && !humanReviewReasons.some((entry) => entry.includes("\u6700\u7EC8 diff"))) {
    humanReviewReasons.push("\u805A\u5408 diff \u8D85\u8FC7\u5B89\u5168\u4E0A\u4E0B\u6587\u4E0A\u9650\uFF0C\u65E0\u6CD5\u5B8C\u6574\u9A8C\u6536");
  }
  const workspaceEvidence = plan.map((workspace) => {
    const state = workspaceState.get(workspace.id);
    const inspection = state.inspection;
    return {
      workspaceId: workspace.id,
      root: workspace.root,
      branch: workspace.branch,
      wave: workspace.wave,
      dependsOnWorkspaceIds: workspace.dependsOnWorkspaceIds,
      baseCommit: workspace.baseCommit,
      headCommit: inspection?.repository.head ?? null,
      currentBranch: inspection?.repository.branch ?? null,
      changedFiles: inspection?.changed.map((entry) => entry.path).sort() ?? [],
      preExistingUnchanged: inspection?.unchangedPreExisting.sort() ?? [],
      ambiguousPaths: inspection?.ambiguous.sort() ?? [],
      diffSha256: inspection?.diffBundle.sha256 ?? null,
      status: state.status,
      blockers: state.blockers,
      humanReviewReasons: state.humanReviewReasons
    };
  });
  const changedFiles = workspaceEvidence.flatMap((entry) => entry.changedFiles.map((filePath) => ({
    workspaceId: entry.workspaceId,
    path: filePath
  }))).sort((left, right) => left.workspaceId.localeCompare(right.workspaceId) || left.path.localeCompare(right.path));
  const status = blockers.length > 0 ? "FAIL" : humanReviewReasons.length > 0 ? "NEED_HUMAN_REVIEW" : "PASS";
  const evidence = {
    schemaVersion: 2,
    task,
    round,
    mode: frozen.mode,
    status,
    frozenFingerprint: frozen.frozenFingerprint,
    changedFiles,
    diffSha256: bundle?.sha256 ?? null,
    diffWorkspaces: bundle?.workspaces ?? [],
    checks: {
      frozenFingerprint: true,
      workspaceCoverage: coverageValid,
      dependencyGraph,
      protectedPaths: !blockers.some((entry) => entry.includes("\u53D7\u4FDD\u62A4\u8DEF\u5F84")),
      forbiddenPaths: !blockers.some((entry) => entry.includes("\u654F\u611F\u8DEF\u5F84")),
      scope: !blockers.some((entry) => entry.includes("\u5141\u8BB8\u8303\u56F4")),
      attribution: plan.length > 0 && workspaceEvidence.every((entry) => entry.ambiguousPaths.length === 0),
      dependencies: plan.length > 0 && workspaceEvidence.every((entry) => entry.status === "PASS")
    },
    workspaces: workspaceEvidence,
    tests,
    blockers,
    humanReviewReasons,
    createdAt: iso(now)
  };
  const report = renderMultiSelfCheck(evidence);
  evidence.reportFingerprint = fingerprint({ text: report });
  evidence.evidenceFingerprint = fingerprint(evidence);
  const evidencePath = await writeRoundFile(directory, "gate-evidence.json", json5(evidence), { fs });
  const reportPath = await writeRoundFile(directory, "self-check-report.md", report, { fs });
  return { status, task, round, evidencePath, reportPath, evidenceFingerprint: evidence.evidenceFingerprint };
}
async function runSelfCheck({
  root,
  task,
  round: suppliedRound,
  snapshot: snapshotSource,
  timeoutMs = 12e4,
  fs = fsPromises10,
  runProcessImpl = runProcess,
  now
} = {}) {
  const round = normalizeRound(suppliedRound);
  const frozen = await loadFrozenTask({ root, task, fs });
  const config = await loadConfig(root);
  const directory = await roundDirectory({ root, task, round, fs });
  const blockers = [];
  const humanReviewReasons = [];
  let snapshot;
  try {
    snapshot = await readSnapshot({ root, task, round, source: snapshotSource, frozen, fs });
  } catch (error) {
    humanReviewReasons.push(`${error.code ?? "SNAPSHOT_INVALID"}\uFF1A${error.message}`);
  }
  if (snapshot?.schemaVersion === 2) {
    return runMultiWorkspaceSelfCheck({
      root,
      task,
      round,
      frozen,
      snapshot,
      config,
      directory,
      fs,
      runProcessImpl,
      timeoutMs,
      now
    });
  }
  let repository = null;
  let changed2 = [];
  let ambiguousPaths = [];
  let preExistingUnchanged = [];
  let diffBundle = null;
  try {
    repository = await currentStatus({ root, git: config.tools.git, runProcessImpl, timeoutMs });
    const runtimePrefix = `.ai-dev-loop/${task}/`;
    const relevant = repository.entries.filter((entry) => !entry.path.startsWith(runtimePrefix));
    const protectedChanged = relevant.filter((entry) => matchesAny(entry.path, config.protectedPaths));
    const forbiddenChanged = relevant.filter((entry) => matchesAny(entry.path, config.forbiddenPaths) || policyForbidden(entry.path));
    if (protectedChanged.length > 0) blockers.push(`\u53D7\u4FDD\u62A4\u8DEF\u5F84\u53D1\u751F\u6539\u52A8\uFF1A${protectedChanged.map((entry) => entry.path).join("\u3001")}`);
    if (forbiddenChanged.length > 0) blockers.push(`\u654F\u611F\u8DEF\u5F84\u53D1\u751F\u6539\u52A8\uFF08\u672A\u8BFB\u53D6\u5185\u5BB9\uFF09\uFF1A${forbiddenChanged.map((entry) => entry.path).join("\u3001")}`);
    const enriched = await enrichStatus(root, relevant, {
      fs,
      skipPatterns: config.forbiddenPaths,
      skipPaths: forbiddenChanged.map((entry) => entry.path)
    });
    if (snapshot) {
      if (repository.head !== snapshot.baseCommit) blockers.push("\u5F53\u524D HEAD \u4E0E\u5F00\u53D1\u524D\u5FEB\u7167 commit \u4E0D\u4E00\u81F4");
      const attributed = attributeChanges(enriched, snapshot);
      changed2 = attributed.changed;
      ambiguousPaths = attributed.ambiguous;
      preExistingUnchanged = attributed.unchangedPreExisting;
      if (ambiguousPaths.length > 0) humanReviewReasons.push(`\u5F00\u53D1\u524D\u5DF2\u6709\u6539\u52A8\u5728\u672C\u8F6E\u53D1\u751F\u53D8\u5316\u6216\u6D88\u5931\uFF1A${ambiguousPaths.join("\u3001")}`);
      const outOfScope = changed2.filter((entry) => !matchesAny(entry.path, snapshot.allowedPaths));
      if (outOfScope.length > 0) blockers.push(`\u672C\u8F6E\u6539\u52A8\u8D85\u51FA\u5141\u8BB8\u8303\u56F4\uFF1A${outOfScope.map((entry) => entry.path).join("\u3001")}`);
      if (frozen.mode === "light" && changed2.length > 3) blockers.push("Light \u6A21\u5F0F\u5B9E\u9645\u6539\u52A8\u8D85\u8FC7\u4E09\u4E2A\u6587\u4EF6\uFF0C\u5FC5\u987B\u5347\u7EA7\u4E3A Full");
      if (changed2.length === 0) blockers.push("\u672A\u68C0\u6D4B\u5230\u53EF\u5F52\u5C5E\u4E8E\u672C\u8F6E\u7684\u4ED3\u5E93\u6539\u52A8");
      const forbiddenSet = new Set(forbiddenChanged.map((entry) => entry.path));
      const safeChanged = changed2.filter((entry) => !forbiddenSet.has(entry.path));
      diffBundle = await buildDiffBundle({ root, git: config.tools.git, changed: safeChanged, runProcessImpl, timeoutMs, fs });
      if (diffBundle.truncated) humanReviewReasons.push("\u6700\u7EC8 diff \u8D85\u8FC7\u5B89\u5168\u4E0A\u4E0B\u6587\u4E0A\u9650\uFF0C\u65E0\u6CD5\u5B8C\u6574\u9A8C\u6536");
    }
  } catch (error) {
    humanReviewReasons.push(`${error.code ?? "GIT_EVIDENCE_FAILED"}\uFF1A\u65E0\u6CD5\u53D6\u5F97\u5B8C\u6574 Git \u8BC1\u636E`);
  }
  const tests = await executeTests(frozen, { root, runProcessImpl, timeoutMs });
  for (const result3 of tests) {
    if (result3.status !== "PASS") blockers.push(`\u6D4B\u8BD5\u5931\u8D25\uFF1A${JSON.stringify(result3.argv)}`);
    if (result3.outputTruncated) humanReviewReasons.push(`\u6D4B\u8BD5\u8F93\u51FA\u88AB\u622A\u65AD\uFF1A${JSON.stringify(result3.argv)}`);
  }
  const forbiddenPaths = !blockers.some((entry) => entry.startsWith("\u654F\u611F\u8DEF\u5F84"));
  const protectedPaths = !blockers.some((entry) => entry.startsWith("\u53D7\u4FDD\u62A4\u8DEF\u5F84"));
  const scope = !blockers.some((entry) => entry.includes("\u5141\u8BB8\u8303\u56F4") || entry.includes("\u8D85\u8FC7\u4E09\u4E2A\u6587\u4EF6"));
  const status = blockers.length > 0 ? "FAIL" : humanReviewReasons.length > 0 ? "NEED_HUMAN_REVIEW" : "PASS";
  const evidence = {
    schemaVersion: 1,
    task,
    round,
    mode: frozen.mode,
    status,
    baseCommit: snapshot?.baseCommit ?? null,
    headCommit: repository?.head ?? null,
    frozenFingerprint: frozen.frozenFingerprint,
    changedFiles: changed2.map((entry) => entry.path).sort(),
    preExistingUnchanged: [...preExistingUnchanged].sort(),
    ambiguousPaths: [...ambiguousPaths].sort(),
    diffSha256: diffBundle?.sha256 ?? null,
    checks: { frozenFingerprint: true, protectedPaths, forbiddenPaths, scope, attribution: ambiguousPaths.length === 0 && Boolean(snapshot) },
    tests,
    blockers,
    humanReviewReasons,
    createdAt: iso(now)
  };
  const report = renderSelfCheck(evidence);
  evidence.reportFingerprint = fingerprint({ text: report });
  evidence.evidenceFingerprint = fingerprint(evidence);
  const evidencePath = await writeRoundFile(directory, "gate-evidence.json", json5(evidence), { fs });
  const reportPath = await writeRoundFile(directory, "self-check-report.md", report, { fs });
  return { status, task, round, evidencePath, reportPath, evidenceFingerprint: evidence.evidenceFingerprint };
}

// src/acceptance/accept.mjs
import * as fsPromises11 from "node:fs/promises";
import path14 from "node:path";
import { tmpdir } from "node:os";
var REVIEW_SCHEMA = Object.freeze({
  type: "object",
  additionalProperties: false,
  required: ["status", "reviewer", "reviewerKind", "isolation", "checkedAcceptanceIds", "counts", "findings", "suggestedTests", "repairInstructions"],
  properties: {
    status: { enum: ["PASS", "FAIL", "NEED_HUMAN_REVIEW"] },
    reviewer: { type: ["string", "null"], pattern: "^[a-z][a-z0-9._-]{0,63}$" },
    reviewerKind: { enum: ["independent-agent", "fresh-subagent", "human-review"] },
    isolation: { enum: ["fresh-read-only-no-development-context", "not-available"] },
    checkedAcceptanceIds: { type: "array", items: { type: "string" }, uniqueItems: true },
    counts: {
      type: "object",
      additionalProperties: false,
      required: ["p0", "p1", "p2"],
      properties: { p0: { type: "integer", minimum: 0 }, p1: { type: "integer", minimum: 0 }, p2: { type: "integer", minimum: 0 } }
    },
    findings: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["id", "severity", "title", "relatedIds", "file", "line", "evidence", "impact", "remediation"],
        properties: {
          id: { type: "string", pattern: "^F-[0-9]{3}$" },
          severity: { enum: ["P0", "P1", "P2"] },
          title: { type: "string", minLength: 1 },
          relatedIds: { type: "array", items: { type: "string" }, uniqueItems: true },
          file: { type: ["string", "null"] },
          line: { type: ["integer", "null"], minimum: 1 },
          evidence: { type: "string", minLength: 1 },
          impact: { type: "string", minLength: 1 },
          remediation: { type: "string", minLength: 1 }
        }
      }
    },
    suggestedTests: { type: "array", items: { type: "string" } },
    repairInstructions: { type: "array", items: { type: "string" } }
  }
});
function nonempty(value) {
  return typeof value === "string" && value.trim().length > 0;
}
function policyForbidden2(filePath) {
  try {
    normalizeBaselineInputPath(filePath);
    return false;
  } catch {
    return true;
  }
}
function exactKeys3(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) && stableJson(Object.keys(value).sort()) === stableJson([...keys].sort());
}
function sameSet(left, right) {
  return left.length === right.length && [...left].sort().every((entry, index) => entry === [...right].sort()[index]);
}
function validateReview(value, frozen, expectedReviewer, expectedKind) {
  const topKeys = ["status", "reviewer", "reviewerKind", "isolation", "checkedAcceptanceIds", "counts", "findings", "suggestedTests", "repairInstructions"];
  const humanReview = value?.reviewerKind === "human-review";
  const validIdentity = humanReview ? value.status === "NEED_HUMAN_REVIEW" && value.reviewer === null && value.isolation === "not-available" : isAgentRuntime(value?.reviewer) && ["independent-agent", "fresh-subagent"].includes(value.reviewerKind) && value.isolation === "fresh-read-only-no-development-context";
  const validTop = exactKeys3(value, topKeys) && ["PASS", "FAIL", "NEED_HUMAN_REVIEW"].includes(value.status) && validIdentity && (expectedReviewer === void 0 || value.reviewer === expectedReviewer) && (expectedKind === void 0 || value.reviewerKind === expectedKind) && Array.isArray(value.checkedAcceptanceIds) && Array.isArray(value.findings) && Array.isArray(value.suggestedTests) && value.suggestedTests.every(nonempty) && Array.isArray(value.repairInstructions) && value.repairInstructions.every(nonempty) && exactKeys3(value.counts, ["p0", "p1", "p2"]) && Object.values(value.counts).every((count) => Number.isInteger(count) && count >= 0);
  if (!validTop) throw new GatedLoopError("REVIEW_INVALID", "Reviewer result does not match the acceptance schema");
  const acceptanceIds = frozen.acceptance.map((entry) => entry.id);
  if (new Set(value.checkedAcceptanceIds).size !== value.checkedAcceptanceIds.length || value.checkedAcceptanceIds.some((id) => !acceptanceIds.includes(id))) {
    throw new GatedLoopError("REVIEW_INVALID", "Reviewer checkedAcceptanceIds are invalid");
  }
  if (value.status !== "NEED_HUMAN_REVIEW" && !sameSet(value.checkedAcceptanceIds, acceptanceIds)) {
    throw new GatedLoopError("REVIEW_INVALID", "PASS or FAIL must cover every frozen acceptance ID");
  }
  const allowedRelated = /* @__PURE__ */ new Set(["SAFETY"]);
  for (const entry of frozen.acceptance) {
    allowedRelated.add(entry.id);
    for (const id of entry.requirementIds) allowedRelated.add(id);
  }
  for (const entry of frozen.tasks) {
    allowedRelated.add(entry.id);
    for (const id of entry.requirementIds) allowedRelated.add(id);
    for (const id of entry.acceptanceIds) allowedRelated.add(id);
  }
  const findingKeys = ["id", "severity", "title", "relatedIds", "file", "line", "evidence", "impact", "remediation"];
  const ids = /* @__PURE__ */ new Set();
  const actual = { p0: 0, p1: 0, p2: 0 };
  for (const finding of value.findings) {
    const valid = exactKeys3(finding, findingKeys) && /^F-\d{3}$/.test(finding.id) && ["P0", "P1", "P2"].includes(finding.severity) && nonempty(finding.title) && Array.isArray(finding.relatedIds) && new Set(finding.relatedIds).size === finding.relatedIds.length && finding.relatedIds.every((id) => allowedRelated.has(id)) && (finding.file === null || nonempty(finding.file)) && (finding.line === null || Number.isInteger(finding.line) && finding.line >= 1) && nonempty(finding.evidence) && nonempty(finding.impact) && nonempty(finding.remediation);
    if (!valid || ids.has(finding.id) || ["P0", "P1"].includes(finding.severity) && finding.relatedIds.length === 0) {
      throw new GatedLoopError("REVIEW_INVALID", "Reviewer finding is invalid or untraceable");
    }
    ids.add(finding.id);
    actual[finding.severity.toLowerCase()]++;
  }
  if (stableJson(actual) !== stableJson(value.counts)) throw new GatedLoopError("REVIEW_INVALID", "Reviewer severity counts do not match findings");
  if (value.status === "PASS" && (actual.p0 > 0 || actual.p1 > 0)) throw new GatedLoopError("REVIEW_INVALID", "PASS cannot contain P0 or P1 findings");
  if (value.status === "FAIL" && actual.p0 + actual.p1 === 0) throw new GatedLoopError("REVIEW_INVALID", "FAIL requires at least one P0 or P1 finding");
  if (value.status === "NEED_HUMAN_REVIEW" && value.repairInstructions.length === 0) {
    throw new GatedLoopError("REVIEW_INVALID", "NEED_HUMAN_REVIEW must explain the missing evidence or isolation");
  }
  return structuredClone(value);
}
function reviewerPrompt(frozen, evidence, report, diffText) {
  return `\u4F60\u662F\u4E0E\u5F00\u53D1\u8005\u5206\u79BB\u7684\u5168\u65B0\u53EA\u8BFB\u9A8C\u6536 Agent\uFF0C\u4E0D\u5F97\u7EE7\u627F\u9700\u6C42\u5206\u6790\u6216\u5F00\u53D1\u4F1A\u8BDD\u4E0A\u4E0B\u6587\u3002\u82E5\u4F60\u662F\u5BBF\u4E3B\u521B\u5EFA\u7684\u5168\u65B0\u5B50 Agent\uFF0CreviewerKind \u8F93\u51FA fresh-subagent\uFF1B\u5426\u5219\u8F93\u51FA independent-agent\u3002isolation \u5FC5\u987B\u8F93\u51FA fresh-read-only-no-development-context\u3002\u4E0D\u5F97\u4FEE\u6539\u6587\u4EF6\u3001\u4FEE\u590D\u4EE3\u7801\u3001\u63D0\u4EA4\u3001\u63A8\u9001\u3001\u5408\u5E76\u6216\u53D1\u5E03\u3002
\u6839\u636E\u51BB\u7ED3\u6388\u6743\u5BA1\u67E5\u6700\u7EC8\u6539\u52A8\uFF0C\u9010\u9879\u68C0\u67E5\u5168\u90E8\u9A8C\u6536 ID\uFF0C\u5E76\u68C0\u67E5\u8FB9\u754C\u3001\u5F02\u5E38\u3001\u6743\u9650\u3001\u5B89\u5168\u3001\u6570\u636E\u3001\u517C\u5BB9\u6027\u3001\u5E76\u53D1\u548C\u6D4B\u8BD5\u5145\u5206\u6027\u3002
\u6309\u7ED9\u5B9A JSON schema \u8F93\u51FA\u3002P0/P1 \u5FC5\u987B FAIL\uFF1B\u53EA\u6709 P2 \u53EF\u4EE5 PASS\uFF1B\u8BC1\u636E\u3001\u9694\u79BB\u6216\u5F52\u5C5E\u4E0D\u8DB3\u65F6 NEED_HUMAN_REVIEW\u3002

# \u51BB\u7ED3\u6388\u6743\uFF08${frozen.authorityName}\uFF09
${frozen.authority}

# acceptance.json
${JSON.stringify(frozen.acceptance, null, 2)}

# tasks.json
${JSON.stringify(frozen.tasks, null, 2)}

# gate-evidence.json
${JSON.stringify(evidence, null, 2)}

# self-check-report.md
${report}

# \u6700\u7EC8\u771F\u5B9E diff
${diffText}
`;
}
async function invokeCodex({ command, prompt, fs, runProcessImpl, timeoutMs }) {
  const temporary = await fs.mkdtemp(path14.join(tmpdir(), "gated-loop-review-"));
  const schemaPath = path14.join(temporary, "schema.json");
  const outputPath = path14.join(temporary, "review.json");
  try {
    await fs.writeFile(schemaPath, json5(REVIEW_SCHEMA), { flag: "wx" });
    await runProcessImpl(command, [
      "exec",
      "--sandbox",
      "read-only",
      "--ephemeral",
      "--ignore-rules",
      "--skip-git-repo-check",
      "--color",
      "never",
      "--output-schema",
      schemaPath,
      "--output-last-message",
      outputPath,
      "-C",
      temporary,
      "-"
    ], { cwd: temporary, timeoutMs, captureOutput: true, input: prompt });
    return JSON.parse(await fs.readFile(outputPath, "utf8"));
  } finally {
    await fs.rm(temporary, { recursive: true, force: true }).catch(() => {
    });
  }
}
async function invokeClaude({ command, prompt, fs, runProcessImpl, timeoutMs }) {
  const temporary = await fs.mkdtemp(path14.join(tmpdir(), "gated-loop-review-"));
  try {
    const result3 = await runProcessImpl(command, [
      "-p",
      "--safe-mode",
      "--no-session-persistence",
      "--permission-mode",
      "plan",
      "--tools",
      "",
      "--output-format",
      "json",
      "--json-schema",
      JSON.stringify(REVIEW_SCHEMA)
    ], { cwd: temporary, timeoutMs, captureOutput: true, input: prompt });
    const parsed = JSON.parse(result3.stdout);
    return parsed.structured_output ?? parsed.result ?? parsed;
  } finally {
    await fs.rm(temporary, { recursive: true, force: true }).catch(() => {
    });
  }
}
function unavailable(error) {
  return error?.code === "PROCESS_SPAWN_FAILED" && error.details?.causeCode === "ENOENT";
}
async function autoReview({ preference, prompt, config, fs, runProcessImpl, timeoutMs }) {
  if (preference === "codex") return { reviewer: "codex", reviewerKind: "independent-agent", value: await invokeCodex({ command: config.tools.codex, prompt, fs, runProcessImpl, timeoutMs }) };
  if (preference === "claude") return { reviewer: "claude", reviewerKind: "independent-agent", value: await invokeClaude({ command: config.tools.claude, prompt, fs, runProcessImpl, timeoutMs }) };
  try {
    return { reviewer: "codex", reviewerKind: "independent-agent", value: await invokeCodex({ command: config.tools.codex, prompt, fs, runProcessImpl, timeoutMs }) };
  } catch (error) {
    if (!unavailable(error)) throw error;
    return { reviewer: "claude", reviewerKind: "independent-agent", value: await invokeClaude({ command: config.tools.claude, prompt, fs, runProcessImpl, timeoutMs }) };
  }
}
function initialReviewPlan({ reviewer, reviewResult, reviewerInvoker }) {
  if (reviewResult) return {
    schemaVersion: 1,
    requested: "review-result",
    route: "provided-result",
    status: "PLANNED",
    selectedReviewer: reviewResult.reviewer ?? null,
    reviewerKind: reviewResult.reviewerKind ?? null,
    isolation: reviewResult.isolation ?? null,
    reason: "\u5BBF\u4E3B\u63D0\u4F9B\u4E86\u5DF2\u5B8C\u6210\u7684\u7ED3\u6784\u5316\u9A8C\u6536\u7ED3\u679C\u3002"
  };
  if (reviewer === "human") return {
    schemaVersion: 1,
    requested: "human",
    route: "human",
    status: "PLANNED",
    selectedReviewer: null,
    reviewerKind: "human-review",
    isolation: "not-available",
    reason: "\u7528\u6237\u660E\u786E\u9009\u62E9\u4EBA\u5DE5\u8BED\u4E49\u9A8C\u6536\u3002"
  };
  if (reviewerInvoker) return {
    schemaVersion: 1,
    requested: reviewer ?? "host-capability",
    route: "host-agent",
    status: "PLANNED",
    selectedReviewer: null,
    reviewerKind: null,
    isolation: "fresh-read-only-no-development-context",
    reason: "\u5BBF\u4E3B\u63D0\u4F9B\u4E86\u53EF\u521B\u5EFA\u5168\u65B0\u72EC\u7ACB Agent \u6216\u5B50 Agent \u7684\u9A8C\u6536\u80FD\u529B\u3002"
  };
  if (reviewer === "codex" || reviewer === "claude") return {
    schemaVersion: 1,
    requested: reviewer,
    route: "external-cli",
    status: "PLANNED",
    selectedReviewer: reviewer,
    reviewerKind: "independent-agent",
    isolation: "fresh-read-only-no-development-context",
    reason: `\u7528\u6237\u660E\u786E\u9009\u62E9\u53EF\u9009\u7684 ${reviewer} CLI \u9A8C\u6536\u9002\u914D\u5668\u3002`
  };
  if (reviewer === "auto") return {
    schemaVersion: 1,
    requested: "auto",
    route: "external-cli-auto",
    status: "PLANNED",
    selectedReviewer: null,
    reviewerKind: "independent-agent",
    isolation: "fresh-read-only-no-development-context",
    reason: "\u7528\u6237\u660E\u786E\u5141\u8BB8 CLI \u6309 Codex\u3001Claude \u7684\u987A\u5E8F\u63A2\u6D4B\u53EF\u9009\u9A8C\u6536\u9002\u914D\u5668\u3002"
  };
  return {
    schemaVersion: 1,
    requested: "default",
    route: "human",
    status: "PLANNED",
    selectedReviewer: null,
    reviewerKind: "human-review",
    isolation: "not-available",
    reason: "\u672A\u63D0\u4F9B\u9694\u79BB\u9A8C\u6536\u80FD\u529B\uFF1B\u9ED8\u8BA4\u4E0D\u626B\u63CF\u6216\u542F\u52A8\u5916\u90E8 Agent\uFF0C\u8F6C\u5165\u4EBA\u5DE5\u8BED\u4E49\u9A8C\u6536\u3002"
  };
}
function completedReviewPlan(plan, invoked) {
  return {
    ...plan,
    status: "COMPLETED",
    selectedReviewer: invoked.reviewer,
    reviewerKind: invoked.reviewerKind,
    isolation: invoked.value.isolation,
    reason: "\u5DF2\u53D6\u5F97\u5E76\u6821\u9A8C\u5168\u65B0\u53EA\u8BFB\u65E0\u5F00\u53D1\u4E0A\u4E0B\u6587\u7684\u8BED\u4E49\u9A8C\u6536\u7ED3\u679C\u3002"
  };
}
function failedReviewPlan(plan, error, stage) {
  const reason = `${error.code ?? "ACCEPTANCE_FAILED"}\uFF1A${error.message ?? "\u8BED\u4E49\u9A8C\u6536\u65E0\u6CD5\u5B8C\u6210"}`;
  return { ...plan, status: stage === "evidence" ? "BLOCKED" : "UNAVAILABLE", reason };
}
function fallbackReview(reason) {
  return {
    status: "NEED_HUMAN_REVIEW",
    reviewer: null,
    reviewerKind: "human-review",
    isolation: "not-available",
    checkedAcceptanceIds: [],
    counts: { p0: 0, p1: 0, p2: 0 },
    findings: [],
    suggestedTests: [],
    repairInstructions: [reason]
  };
}
function findingSection(findings, severity) {
  const selected = findings.filter((entry) => entry.severity === severity);
  if (selected.length === 0) return "- \u65E0";
  return selected.map((entry) => {
    const location = entry.file ? `${entry.file}${entry.line ? `:${entry.line}` : ""}` : "\u65E0\u56FA\u5B9A\u4F4D\u7F6E";
    return `- **${entry.id} ${entry.title}**\uFF08${entry.relatedIds.join("\u3001") || "\u65E0\u5173\u8054 ID"}\uFF1B${location}\uFF09
  - \u8BC1\u636E\uFF1A${entry.evidence}
  - \u5F71\u54CD\uFF1A${entry.impact}
  - \u4FEE\u590D\uFF1A${entry.remediation}`;
  }).join("\n");
}
function renderAcceptance(task, round, review) {
  const tests = review.suggestedTests.length > 0 ? review.suggestedTests.map((entry) => `- ${entry}`).join("\n") : "- \u65E0";
  const repairs = review.repairInstructions.length > 0 ? review.repairInstructions.map((entry) => `- ${entry}`).join("\n") : "- \u65E0\u9700\u4FEE\u590D";
  const title = review.reviewerKind === "human-review" ? "\u4EBA\u5DE5\u8BED\u4E49\u9A8C\u6536\u5F85\u529E\u62A5\u544A" : "\u72EC\u7ACB\u8BED\u4E49\u9A8C\u6536\u62A5\u544A";
  const reviewer = review.reviewer ?? "\u672A\u542F\u52A8\uFF08\u4EBA\u5DE5\u9A8C\u6536\uFF09";
  return `# ${task} ${round} ${title}

## \u7ED3\u8BBA
${review.status}

## \u5BA1\u67E5\u8EAB\u4EFD
- reviewer: ${reviewer}
- reviewerKind: ${review.reviewerKind}
- isolation: ${review.isolation}

## \u4E25\u91CD\u7EA7\u522B\u6C47\u603B
| P0 | P1 | P2 |
| ---: | ---: | ---: |
| ${review.counts.p0} | ${review.counts.p1} | ${review.counts.p2} |

## \u5DF2\u68C0\u67E5\u5185\u5BB9
- \u9A8C\u6536 ID\uFF1A${review.checkedAcceptanceIds.join("\u3001") || "\u672A\u5B8C\u6210"}
- \u673A\u68B0\u81EA\u68C0\uFF1A[self-check-report.md](self-check-report.md)
- \u6D4B\u8BD5\u8BC1\u636E\uFF1A[gate-evidence.json](gate-evidence.json)

## P0 \u4E25\u91CD\u95EE\u9898
${findingSection(review.findings, "P0")}

## P1 \u963B\u65AD\u95EE\u9898
${findingSection(review.findings, "P1")}

## P2 \u975E\u963B\u65AD\u5EFA\u8BAE
${findingSection(review.findings, "P2")}

## \u5EFA\u8BAE\u8865\u5145\u6D4B\u8BD5
${tests}

## \u7ED9\u5F00\u53D1 Agent \u7684\u4FEE\u590D\u6307\u4EE4
${repairs}
`;
}
function manualAcceptanceStatus(status) {
  if (status === "PASS") return "WAITING_FOR_MANUAL_ACCEPTANCE";
  if (status === "FAIL") return "BLOCKED_BY_P0_P1";
  return "NEED_HUMAN_REVIEW";
}
function renderFinalAcceptance(task, round, frozen, review, verified) {
  const roundPath = `rounds/${round}`;
  const authority = frozen.authorityName;
  const tests = review.suggestedTests.length > 0 ? review.suggestedTests.map((entry) => `- ${entry}`).join("\n") : "- \u65E0";
  const repairs = review.repairInstructions.length > 0 ? review.repairInstructions.map((entry) => `- ${entry}`).join("\n") : "- \u65E0\u9700\u4FEE\u590D";
  const reviewer = review.reviewer ?? "\u672A\u542F\u52A8\uFF08\u4EBA\u5DE5\u9A8C\u6536\uFF09";
  const operation = review.status === "PASS" ? "\u72EC\u7ACB\u9A8C\u6536\u5DF2\u901A\u8FC7\uFF0C\u7B49\u5F85\u7528\u6237\u4EBA\u5DE5\u786E\u8BA4\u3002PASS \u4E0D\u6388\u6743\u81EA\u52A8\u63D0\u4EA4\u3001\u63A8\u9001\u3001\u5408\u5E76\u6216\u53D1\u5E03\u3002" : review.status === "FAIL" ? "\u5B58\u5728 P0/P1 \u963B\u65AD\u9879\uFF0C\u4FEE\u590D\u5E76\u91CD\u65B0\u5B8C\u6210\u673A\u68B0\u95E8\u7981\u548C\u72EC\u7ACB\u9A8C\u6536\u524D\uFF0C\u4E0D\u80FD\u8FDB\u5165\u4EBA\u5DE5\u5B8C\u6210\u786E\u8BA4\u3002" : review.reviewerKind === "human-review" ? "\u673A\u68B0\u95E8\u7981\u4E0D\u56E0\u6B64\u5931\u6548\uFF0C\u4F46\u5C1A\u672A\u5B8C\u6210\u72EC\u7ACB\u8BED\u4E49\u9A8C\u6536\u3002\u8BF7\u7531\u7528\u6237\u4EBA\u5DE5\u5BA1\u67E5\u51BB\u7ED3\u9A8C\u6536\u9879\u3001\u771F\u5B9E diff\u3001\u673A\u68B0\u8BC1\u636E\u548C\u672C\u62A5\u544A\uFF1B\u4E0D\u5F97\u628A\u6B64\u72B6\u6001\u8868\u8FF0\u4E3A\u72EC\u7ACB\u9A8C\u6536 PASS\u3002" : "\u8BC1\u636E\u3001\u9694\u79BB\u6216\u5BA1\u67E5\u8FC7\u7A0B\u4E0D\u8DB3\uFF0C\u9700\u8981\u4EBA\u5DE5\u5BA1\u67E5\u540E\u51B3\u5B9A\u91CD\u8BD5\u3001\u4FEE\u590D\u6216\u7EC8\u6B62\u3002";
  return `# ${task} \u6700\u7EC8\u9A8C\u6536\u62A5\u544A

> \u5F53\u524D\u9A8C\u6536\u7ED3\u8BBA\uFF1A**${review.status}**
>
> \u5F53\u524D\u9A8C\u6536\u8F6E\u6B21\uFF1A**${round}**
>
> \u4EBA\u5DE5\u786E\u8BA4\u72B6\u6001\uFF1A**${manualAcceptanceStatus(review.status)}**

## \u9A8C\u6536\u6458\u8981

| \u9879\u76EE | \u7ED3\u679C |
| --- | --- |
| \u4EFB\u52A1\u6A21\u5F0F | ${frozen.mode} |
| \u673A\u68B0\u95E8\u7981 | ${verified?.evidence?.status ?? "UNVERIFIED"} |
| \u72EC\u7ACB\u5BA1\u67E5\u8005 | ${reviewer} |
| \u5BA1\u67E5\u8005\u7C7B\u578B | ${review.reviewerKind} |
| \u4E0A\u4E0B\u6587\u9694\u79BB | ${review.isolation} |
| P0 / P1 / P2 | ${review.counts.p0} / ${review.counts.p1} / ${review.counts.p2} |
| \u5DF2\u68C0\u67E5\u9A8C\u6536 ID | ${review.checkedAcceptanceIds.join("\u3001") || "\u672A\u5B8C\u6210"} |

## P0 \u4E25\u91CD\u95EE\u9898
${findingSection(review.findings, "P0")}

## P1 \u963B\u65AD\u95EE\u9898
${findingSection(review.findings, "P1")}

## P2 \u975E\u963B\u65AD\u5EFA\u8BAE
${findingSection(review.findings, "P2")}

## \u5EFA\u8BAE\u8865\u5145\u6D4B\u8BD5
${tests}

## \u4FEE\u590D\u6307\u4EE4
${repairs}

## \u4EBA\u5DE5\u64CD\u4F5C\u7ED3\u8BBA

${operation}

## \u8BC1\u636E\u5BFC\u822A

- \u51BB\u7ED3\u6388\u6743\uFF1A[${authority}](${authority})
- \u5F00\u53D1\u603B\u89C8\uFF1A[development-overview.md](development-overview.md)
- \u5F00\u53D1\u8FDB\u5EA6\uFF1A[progress.md](progress.md)
- \u672C\u8F6E\u673A\u68B0\u81EA\u68C0\uFF1A[self-check-report.md](${roundPath}/self-check-report.md)
- \u672C\u8F6E\u673A\u68B0\u8BC1\u636E\uFF1A[gate-evidence.json](${roundPath}/gate-evidence.json)
- \u672C\u8F6E\u9A8C\u6536\u8DEF\u7531\uFF1A[review-plan.json](${roundPath}/review-plan.json)
- \u672C\u8F6E\u8BED\u4E49\u9A8C\u6536\uFF1A[acceptance-report.md](${roundPath}/acceptance-report.md)
- \u672C\u8F6E\u7ED3\u6784\u5316\u5BA1\u67E5\uFF1A[review.json](${roundPath}/review.json)

\u672C\u6587\u4EF6\u7531 \`gated-loop accept\` \u6839\u636E\u5F53\u524D\u8F6E\u6B21\u7684\u5DF2\u6821\u9A8C\u7ED3\u679C\u81EA\u52A8\u5237\u65B0\u3002\u8F6E\u6B21\u62A5\u544A\u4E0E JSON \u662F\u539F\u59CB\u8BC1\u636E\uFF0C\u672C\u6587\u4EF6\u662F\u7ED9\u4EBA\u5DE5\u67E5\u770B\u7684\u6700\u65B0\u6C47\u603B\u5165\u53E3\u3002
`;
}
async function verifiedEvidence({ root, task, round, frozen, config, snapshotSource, fs, runProcessImpl, timeoutMs }) {
  const relative = path14.join(".ai-dev-loop", task, "rounds", round);
  const evidence = JSON.parse((await readSafeRegularFile(root, path14.join(relative, "gate-evidence.json"), { fs })).toString("utf8"));
  const { evidenceFingerprint, ...unsigned } = evidence;
  if (evidence.status !== "PASS" || evidence.task !== task || evidence.round !== round || evidence.frozenFingerprint !== frozen.frozenFingerprint || evidenceFingerprint !== fingerprint(unsigned)) {
    throw new GatedLoopError("SELF_CHECK_NOT_PASS", "A valid PASS self-check is required before acceptance");
  }
  const snapshot = await readSnapshot({ root, task, round, source: snapshotSource, frozen, fs });
  if (snapshot.schemaVersion === 2) {
    const plan = await loadWorkspacePlan({ root, task, round, snapshot, frozen, fs });
    const inspections = [];
    for (const workspace of plan) {
      const inspection = await inspectWorkspace({
        coordinatorRoot: root,
        task,
        workspace,
        git: config.tools.git,
        protectedPaths: config.protectedPaths,
        forbiddenPaths: config.forbiddenPaths,
        isPolicyForbidden: policyForbidden2,
        runProcessImpl,
        timeoutMs,
        fs
      });
      if (inspection.repository.head !== workspace.baseCommit || inspection.repository.branch !== workspace.branch || inspection.protectedChanged.length > 0 || inspection.forbiddenChanged.length > 0 || inspection.outOfScope.length > 0 || inspection.ambiguous.length > 0 || inspection.changed.length === 0 || inspection.diffBundle.truncated) {
        throw new GatedLoopError("ACCEPTANCE_EVIDENCE_CHANGED", `Workspace evidence changed after self-check: ${workspace.id}`);
      }
      inspections.push(inspection);
    }
    const bundle2 = aggregateDiffBundles(inspections);
    const currentPaths2 = inspections.flatMap((inspection) => inspection.changed.map((entry) => ({
      workspaceId: inspection.workspace.id,
      path: entry.path
    }))).sort((left, right) => left.workspaceId.localeCompare(right.workspaceId) || left.path.localeCompare(right.path));
    const currentWorkspaces = inspections.map((inspection) => ({
      workspaceId: inspection.workspace.id,
      headCommit: inspection.repository.head,
      currentBranch: inspection.repository.branch,
      changedFiles: inspection.changed.map((entry) => entry.path).sort(),
      diffSha256: inspection.diffBundle.sha256
    })).sort((left, right) => left.workspaceId.localeCompare(right.workspaceId));
    const evidenceWorkspaces = [...evidence.workspaces ?? []].map((entry) => ({
      workspaceId: entry.workspaceId,
      headCommit: entry.headCommit,
      currentBranch: entry.currentBranch,
      changedFiles: entry.changedFiles,
      diffSha256: entry.diffSha256
    })).sort((left, right) => left.workspaceId.localeCompare(right.workspaceId));
    if (bundle2.truncated || bundle2.sha256 !== evidence.diffSha256 || stableJson(currentPaths2) !== stableJson(evidence.changedFiles) || stableJson(currentWorkspaces) !== stableJson(evidenceWorkspaces)) {
      throw new GatedLoopError("ACCEPTANCE_EVIDENCE_CHANGED", "Multi-workspace evidence changed after self-check");
    }
    const report2 = (await readSafeRegularFile(root, path14.join(relative, "self-check-report.md"), { fs })).toString("utf8");
    if (evidence.reportFingerprint !== fingerprint({ text: report2 })) {
      throw new GatedLoopError("ACCEPTANCE_EVIDENCE_CHANGED", "Self-check report changed after the mechanical gate");
    }
    return { evidence, report: report2, bundle: bundle2 };
  }
  const repository = await currentStatus({ root, git: config.tools.git, runProcessImpl, timeoutMs });
  const runtimePrefix = `.ai-dev-loop/${task}/`;
  const relevant = repository.entries.filter((entry) => !entry.path.startsWith(runtimePrefix));
  const forbiddenChanged = relevant.filter((entry) => matchesAny(entry.path, config.forbiddenPaths) || policyForbidden2(entry.path));
  if (forbiddenChanged.length > 0) {
    throw new GatedLoopError("ACCEPTANCE_EVIDENCE_CHANGED", "Sensitive changed paths prevent independent acceptance");
  }
  const enriched = await enrichStatus(root, relevant, {
    fs,
    skipPatterns: config.forbiddenPaths,
    skipPaths: forbiddenChanged.map((entry) => entry.path)
  });
  const attributed = attributeChanges(enriched, snapshot);
  const bundle = await buildDiffBundle({ root, git: config.tools.git, changed: attributed.changed, runProcessImpl, timeoutMs, fs });
  const currentPaths = attributed.changed.map((entry) => entry.path).sort();
  if (repository.head !== evidence.headCommit || attributed.ambiguous.length > 0 || bundle.truncated || !sameSet(currentPaths, evidence.changedFiles) || bundle.sha256 !== evidence.diffSha256) {
    throw new GatedLoopError("ACCEPTANCE_EVIDENCE_CHANGED", "Repository evidence changed after self-check");
  }
  const report = (await readSafeRegularFile(root, path14.join(relative, "self-check-report.md"), { fs })).toString("utf8");
  if (evidence.reportFingerprint !== fingerprint({ text: report })) {
    throw new GatedLoopError("ACCEPTANCE_EVIDENCE_CHANGED", "Self-check report changed after the mechanical gate");
  }
  return { evidence, report, bundle };
}
async function runAcceptance({
  root,
  task,
  round: suppliedRound,
  snapshot: snapshotSource,
  reviewer,
  reviewResult,
  timeoutMs = 3e5,
  fs = fsPromises11,
  runProcessImpl = runProcess,
  reviewerInvoker
} = {}) {
  const round = normalizeRound(suppliedRound);
  const frozen = await loadFrozenTask({ root, task, fs });
  const config = await loadConfig(root);
  const directory = await roundDirectory({ root, task, round, fs });
  let plan = initialReviewPlan({ reviewer, reviewResult, reviewerInvoker });
  let planPath = await writeRoundFile(directory, "review-plan.json", json5(plan), { fs });
  let stage = "evidence";
  let verified;
  let review;
  try {
    verified = await verifiedEvidence({ root, task, round, frozen, config, snapshotSource, fs, runProcessImpl, timeoutMs });
    stage = "review";
    const prompt = reviewerPrompt(frozen, verified.evidence, verified.report, verified.bundle.text);
    let invoked;
    if (reviewResult) invoked = { reviewer: reviewResult.reviewer, reviewerKind: reviewResult.reviewerKind, value: reviewResult };
    else if (reviewer === "human" || !reviewer && !reviewerInvoker) {
      throw new GatedLoopError("INDEPENDENT_REVIEW_UNAVAILABLE", "\u672A\u63D0\u4F9B\u5168\u65B0\u9694\u79BB Agent \u6216\u5B50 Agent\uFF1B\u5DF2\u8F6C\u5165\u4EBA\u5DE5\u8BED\u4E49\u9A8C\u6536");
    } else if (reviewerInvoker) invoked = await reviewerInvoker({ prompt, schema: REVIEW_SCHEMA, preference: reviewer });
    else invoked = await autoReview({ preference: reviewer, prompt, config, fs, runProcessImpl, timeoutMs });
    review = validateReview(invoked.value, frozen, invoked.reviewer, invoked.reviewerKind);
    plan = completedReviewPlan(plan, invoked);
  } catch (error) {
    plan = failedReviewPlan(plan, error, stage);
    review = fallbackReview(plan.reason);
  }
  planPath = await writeRoundFile(directory, "review-plan.json", json5(plan), { fs });
  const reviewPath = await writeRoundFile(directory, "review.json", json5(review), { fs });
  const reportPath = await writeRoundFile(directory, "acceptance-report.md", renderAcceptance(task, round, review), { fs });
  const finalReportPath = await writeRoundFile(
    frozen.taskPackage.target,
    "final-acceptance-report.md",
    renderFinalAcceptance(task, round, frozen, review, verified),
    { fs }
  );
  return { status: review.status, task, round, reviewer: review.reviewer, counts: review.counts, planPath, reviewPath, reportPath, finalReportPath };
}

// src/work-items/runtime.mjs
import * as fsPromises12 from "node:fs/promises";
import path15 from "node:path";
var WORK_ITEM_REGISTRY_FILE = "work-item-registry.json";
var WORK_ITEMS_DIRECTORY = "work-items";
var GOVERNANCE_DIRECTORY = ".hierarchical-delivery-governance";
var DELIVERY_STATUSES = Object.freeze([
  "NOT_READY",
  "WAITING_FOR_INDEPENDENT_REVIEW",
  "WAITING_FOR_USER_CONFIRMATION",
  "COMPLETED"
]);
var DEVELOPMENT_MODES = Object.freeze(["active", "manual"]);
function fail5(code, message, details = {}) {
  throw new GatedLoopError(code, message, { details });
}
function json6(value) {
  return `${JSON.stringify(value, null, 2)}
`;
}
function timestamp4(now) {
  const value = typeof now === "function" ? now() : now ?? /* @__PURE__ */ new Date();
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) fail5("WORK_ITEM_TIMESTAMP_INVALID", "Work item timestamp is invalid");
  return date.toISOString();
}
async function assertSelfHostingDogfood(root, explicitDogfood, fs) {
  let packageName;
  try {
    const packageJson = JSON.parse((await readSafeRegularFile(root, "package.json", { fs })).toString("utf8"));
    if (typeof packageJson?.name === "string") packageName = packageJson.name;
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "PATH_MISSING" || error instanceof SyntaxError) return;
    throw error;
  }
  const policy = resolveSelfHostingPolicy({ packageName, explicitDogfood });
  if (policy.createsRuntimePackage === false) {
    fail5("SELF_HOSTING_DOGFOOD_REQUIRED", "The hierarchical governance implementation repository requires explicit dogfood for runtime mutations");
  }
}
function emptyRegistry(root, at) {
  return {
    schemaVersion: 2,
    coordinationRoot: path15.resolve(root),
    revision: 0,
    currentFocus: { workItemId: null, purpose: null },
    workItems: [],
    updatedAt: at
  };
}
function registryPath(root) {
  return path15.join(root, GOVERNANCE_DIRECTORY, WORK_ITEM_REGISTRY_FILE);
}
function itemRelativePath(id) {
  return path15.posix.join(WORK_ITEMS_DIRECTORY, id);
}
function itemPath(root, id) {
  return path15.join(root, GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, id);
}
function sortedItems(items) {
  return [...items].sort((left, right) => left.id.localeCompare(right.id));
}
function itemById(registry, id) {
  const item = registry.workItems.find((entry) => entry.id === id);
  if (!item) fail5("WORK_ITEM_NOT_FOUND", `Unknown work item: ${id}`, { id });
  return item;
}
function validEvidenceReference(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const portable = typeof value.path === "string" ? value.path.replaceAll("\\", "/") : "";
  return portable.length > 0 && !path15.posix.isAbsolute(portable) && !portable.split("/").includes("..") && typeof value.sha256 === "string" && /^[a-f0-9]{64}$/.test(value.sha256);
}
function nonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}
function validDevelopmentMode(value, entry) {
  return value && typeof value === "object" && !Array.isArray(value) && value.schemaVersion === 1 && value.taskId === entry.id && value.baselineFingerprint === entry.baselineFingerprint && DEVELOPMENT_MODES.includes(value.mode) && value.confirmedBy === "user" && typeof value.confirmedAt === "string" && !Number.isNaN(Date.parse(value.confirmedAt));
}
function validDeliveryArtifact(action, value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.schemaVersion !== 1) return false;
  if (action === "INDEPENDENT_REVIEW_PASS") {
    return value.kind === "INDEPENDENT_REVIEW" && nonEmptyString(value.reviewer) && value.isolation === "FRESH_READ_ONLY" && value.verdict === "PASS" && value.findings && typeof value.findings === "object" && !Array.isArray(value.findings) && value.findings.p0 === 0 && value.findings.p1 === 0;
  }
  if (action === "HUMAN_REVIEW_ACCEPTED") {
    return value.kind === "HUMAN_REVIEW" && nonEmptyString(value.reviewer) && value.verdict === "ACCEPTED";
  }
  return action === "USER_CONFIRMED" && value.kind === "USER_CONFIRMATION" && nonEmptyString(value.confirmedBy) && value.decision === "CONFIRMED";
}
function validDeliveryEvidence(value, actions) {
  return value && typeof value === "object" && !Array.isArray(value) && actions.includes(value.action) && validEvidenceReference(value.evidence) && validDeliveryArtifact(value.action, value.artifact) && typeof value.recordedAt === "string" && !Number.isNaN(Date.parse(value.recordedAt));
}
function validDelivery(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !DELIVERY_STATUSES.includes(value.status)) return false;
  if (value.status === "NOT_READY" || value.status === "WAITING_FOR_INDEPENDENT_REVIEW") {
    return value.review === null && value.userConfirmation === null;
  }
  const reviewValid = validDeliveryEvidence(
    value.review,
    ["INDEPENDENT_REVIEW_PASS", "HUMAN_REVIEW_ACCEPTED"]
  );
  if (value.status === "WAITING_FOR_USER_CONFIRMATION") {
    return reviewValid && value.userConfirmation === null;
  }
  return reviewValid && validDeliveryEvidence(value.userConfirmation, ["USER_CONFIRMED"]);
}
function validateRegistry(registry, root) {
  const valid = registry && typeof registry === "object" && !Array.isArray(registry) && registry.schemaVersion === 2 && registry.coordinationRoot === path15.resolve(root) && Number.isInteger(registry.revision) && registry.revision >= 0 && Array.isArray(registry.workItems) && registry.currentFocus && typeof registry.currentFocus === "object";
  if (!valid) fail5("WORK_ITEM_REGISTRY_INVALID", "Work item registry is invalid");
  const ids = registry.workItems.map(({ id }) => id);
  const safeId2 = (value) => typeof value === "string" && /^[a-z0-9][a-z0-9._-]*$/.test(value) && !value.endsWith(".") && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/.test(value);
  if (new Set(ids).size !== ids.length || ids.some((id) => !safeId2(id))) {
    fail5("WORK_ITEM_REGISTRY_INVALID", "Work item registry contains duplicate or unsafe IDs");
  }
  const byId = new Map(registry.workItems.map((entry) => [entry.id, entry]));
  for (const entry of registry.workItems) {
    const validEntry = WORK_ITEM_KINDS.includes(entry.kind) && entry.authorityKind === WORK_ITEM_AUTHORITIES[entry.kind] && (entry.parentId === null || safeId2(entry.parentId)) && Array.isArray(entry.childIds) && entry.childIds.every(safeId2) && entry.packagePath === itemRelativePath(entry.id) && typeof entry.baselineFingerprint === "string" && /^[a-f0-9]{64}$/.test(entry.baselineFingerprint) && typeof entry.contractFingerprint === "string" && /^[a-f0-9]{64}$/.test(entry.contractFingerprint);
    if (!validEntry) fail5("WORK_ITEM_REGISTRY_INVALID", `Work item registry entry is invalid: ${entry.id}`);
    const developmentModeValid = entry.kind === "TASK" ? entry.developmentMode === null || validDevelopmentMode(entry.developmentMode, entry) : entry.developmentMode === null;
    if (!developmentModeValid) {
      fail5("WORK_ITEM_REGISTRY_INVALID", `Work item development mode is invalid: ${entry.id}`);
    }
    if (entry.kind === "TASK") {
      const waitingForMode = entry.status === "WAITING_FOR_DEVELOPMENT_MODE_SELECTION";
      const frozenWithoutMode = entry.developmentMode === null && entry.stage === "BASELINE_FROZEN";
      if (waitingForMode !== frozenWithoutMode) {
        fail5("WORK_ITEM_REGISTRY_INVALID", `Task development mode state is inconsistent: ${entry.id}`);
      }
    }
    const deliveryValid = entry.kind === "DELIVERY" ? entry.delivery === void 0 || validDelivery(entry.delivery) : entry.delivery === void 0 || entry.delivery === null;
    if (!deliveryValid) fail5("WORK_ITEM_REGISTRY_INVALID", `Work item delivery state is invalid: ${entry.id}`);
    if (entry.kind === "DELIVERY" && entry.parentId !== null) {
      fail5("WORK_ITEM_REGISTRY_INVALID", "Delivery entries cannot have parents");
    }
    if (entry.kind !== "DELIVERY" && entry.parentId !== null) {
      const parent = byId.get(entry.parentId);
      const expectedParentKind = entry.kind === "CAPABILITY" ? "DELIVERY" : "CAPABILITY";
      if (!parent || parent.kind !== expectedParentKind || !parent.childIds.includes(entry.id)) {
        fail5("WORK_ITEM_REGISTRY_INVALID", `Work item parent relation is invalid: ${entry.id}`);
      }
    }
  }
  const focusId = registry.currentFocus.workItemId;
  if (focusId !== null && (!safeId2(focusId) || !byId.has(focusId))) {
    fail5("WORK_ITEM_REGISTRY_INVALID", "Current focus references an unknown work item");
  }
  return registry;
}
async function ensureRuntimeRoot(root, fs) {
  const rootStat = await fs.lstat(root).catch((error) => {
    if (error.code === "ENOENT") fail5("WORK_ITEM_ROOT_INVALID", "Coordination root must already exist");
    throw error;
  });
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail5("WORK_ITEM_ROOT_INVALID", "Coordination root must be a regular directory");
  const runtimeRoot = await assertSafePath(root, GOVERNANCE_DIRECTORY, { fs });
  await fs.mkdir(runtimeRoot, { recursive: true });
  await assertSafePath(root, GOVERNANCE_DIRECTORY, { fs });
  const itemsRoot = await assertSafePath(root, path15.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY), { fs });
  await fs.mkdir(itemsRoot, { recursive: true });
  await assertSafePath(root, path15.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY), { fs });
  return runtimeRoot;
}
async function assertPersistedDeliveryEvidence(root, registry, fs) {
  for (const entry of registry.workItems.filter(({ kind, delivery }) => kind === "DELIVERY" && delivery && delivery.status !== "NOT_READY" && delivery.status !== "WAITING_FOR_INDEPENDENT_REVIEW")) {
    const records = [entry.delivery.review];
    if (entry.delivery.status === "COMPLETED") records.push(entry.delivery.userConfirmation);
    for (const record of records) {
      let bytes;
      try {
        bytes = await readSafeRegularFile(root, record.evidence.path, { fs });
      } catch {
        fail5("WORK_ITEM_DELIVERY_EVIDENCE_MISSING", `Persisted delivery evidence is unavailable: ${record.evidence.path}`);
      }
      if (sha256Bytes(bytes) !== record.evidence.sha256) {
        fail5("WORK_ITEM_DELIVERY_EVIDENCE_CHANGED", `Persisted delivery evidence changed: ${record.evidence.path}`);
      }
      let artifact;
      try {
        artifact = JSON.parse(bytes.toString("utf8"));
      } catch {
        fail5("WORK_ITEM_DELIVERY_EVIDENCE_INVALID", `Persisted delivery evidence is invalid JSON: ${record.evidence.path}`);
      }
      if (!validDeliveryArtifact(record.action, artifact) || canonicalJson3(artifact) !== canonicalJson3(record.artifact)) {
        fail5("WORK_ITEM_DELIVERY_EVIDENCE_CHANGED", `Persisted delivery evidence no longer matches its registry snapshot: ${record.evidence.path}`);
      }
    }
  }
}
async function assertPersistedDevelopmentModes(root, registry, fs) {
  for (const entry of registry.workItems.filter(({ kind, developmentMode }) => kind === "TASK" && developmentMode !== null)) {
    let artifact;
    try {
      artifact = await readJsonFile(
        itemPath(root, entry.id),
        "development-mode.json",
        fs,
        "WORK_ITEM_DEVELOPMENT_MODE_INVALID"
      );
    } catch {
      fail5("WORK_ITEM_DEVELOPMENT_MODE_INVALID", `${entry.id} development-mode.json is missing or unreadable`);
    }
    if (!validDevelopmentMode(artifact, entry) || canonicalJson3(artifact) !== canonicalJson3(entry.developmentMode)) {
      fail5("WORK_ITEM_DEVELOPMENT_MODE_CHANGED", `${entry.id} development-mode.json changed after confirmation`);
    }
  }
}
async function readRegistryUnlocked(root, fs, { allowMissing = false, now } = {}) {
  const target = registryPath(root);
  let bytes;
  try {
    bytes = await readSafeRegularFile(root, target, { fs });
  } catch (error) {
    if (error.code === "ENOENT" && allowMissing) return emptyRegistry(root, timestamp4(now));
    if (error.code === "ENOENT") fail5("WORK_ITEM_REGISTRY_MISSING", "Work item registry does not exist");
    throw error;
  }
  let registry;
  try {
    registry = JSON.parse(bytes.toString("utf8"));
  } catch {
    fail5("WORK_ITEM_REGISTRY_INVALID", "Work item registry is not valid JSON");
  }
  const validated = validateRegistry(registry, root);
  await assertPersistedDeliveryEvidence(root, validated, fs);
  await assertPersistedDevelopmentModes(root, validated, fs);
  return validated;
}
function renderWorkspaceOverview(registry) {
  const lines = [
    "# Work Item Overview",
    "",
    `> registry revision: ${registry.revision}`,
    `> current focus: ${registry.currentFocus.workItemId ?? "none"}`,
    "",
    "| Work Item | Kind | Parent | Stage | Status | Development mode | Delivery | Direct progress | Descendant progress | Gate | Claim |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
  ];
  for (const item of sortedItems(registry.workItems)) {
    lines.push(`| ${item.id} | ${item.kind} | ${item.parentId ?? "none"} | ${item.stage} | ${item.status} | ${item.developmentMode?.mode ?? "n/a"} | ${item.delivery?.status ?? "n/a"} | ${item.progress.directChildren.verified}/${item.progress.directChildren.total} | ${item.progress.descendants.verified}/${item.progress.descendants.total} | ${item.gate.status} | ${item.claim?.owner ?? "none"} |`);
  }
  lines.push("");
  return lines.join("\n");
}
function renderItemOverview(entry) {
  return [
    `# ${entry.id} Work Item Overview`,
    "",
    `- Kind: ${entry.kind}`,
    `- Authority: ${entry.authorityKind}`,
    `- Parent: ${entry.parentId ?? "none"}`,
    `- Baseline: [baseline.md](baseline.md)`,
    `- Parent contract: ${entry.parentContractFingerprint ?? "none"}`,
    `- Children: ${entry.childIds.join(", ") || "none"}`,
    ""
  ].join("\n");
}
function renderItemProgress(entry) {
  return [
    `# ${entry.id} Progress`,
    "",
    `- Record revision: ${entry.recordRevision}`,
    `- Stage: ${entry.stage}`,
    `- Status: ${entry.status}`,
    `- Delivery: ${entry.delivery?.status ?? "n/a"}`,
    `- Gate: ${entry.gate.status}`,
    `- Development mode: ${entry.developmentMode?.mode ?? "not selected"}`,
    `- Claim: ${entry.claim ? `${entry.claim.owner} / ${entry.claim.operationId}` : "none"}`,
    `- Direct children: ${entry.progress.directChildren.verified}/${entry.progress.directChildren.total} verified; ${entry.progress.directChildren.blocked} blocked; ${entry.progress.directChildren.active} active`,
    `- Descendants: ${entry.progress.descendants.verified}/${entry.progress.descendants.total} verified; ${entry.progress.descendants.blocked} blocked; ${entry.progress.descendants.active} active`,
    `- Updated at: ${entry.updatedAt}`,
    ""
  ].join("\n");
}
function progressCounts(entries) {
  return {
    total: entries.length,
    verified: entries.filter(({ status }) => status === "VERIFIED").length,
    blocked: entries.filter(({ status }) => status === "BLOCKED").length,
    active: entries.filter(({ status }) => status === "CLAIMED" || status === "IMPLEMENTED").length
  };
}
function recomputeRegistryProgress(registry) {
  const byId = new Map(registry.workItems.map((entry) => [entry.id, entry]));
  const descendants = (entry, visited = /* @__PURE__ */ new Set()) => {
    if (visited.has(entry.id)) fail5("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle");
    const nextVisited = new Set(visited).add(entry.id);
    const result3 = [];
    for (const childId of entry.childIds) {
      const child = byId.get(childId) ?? { id: childId, status: "PLANNED", childIds: [] };
      result3.push(child);
      if (byId.has(childId)) result3.push(...descendants(child, nextVisited));
    }
    return result3;
  };
  for (const entry of registry.workItems) {
    const direct = entry.childIds.map((id) => byId.get(id) ?? { id, status: "PLANNED" });
    entry.progress = {
      directChildren: progressCounts(direct),
      descendants: progressCounts(descendants(entry))
    };
  }
}
async function writeRegistryUnlocked(root, registry, fs) {
  recomputeRegistryProgress(registry);
  registry.workItems = sortedItems(registry.workItems);
  await atomicWriteFile(registryPath(root), json6(registry), { fs });
  await atomicWriteFile(
    path15.join(root, GOVERNANCE_DIRECTORY, "workspace-overview.md"),
    renderWorkspaceOverview(registry),
    { fs }
  );
  for (const entry of registry.workItems) {
    const target = itemPath(root, entry.id);
    let stat;
    try {
      stat = await fs.lstat(target);
    } catch (error) {
      if (error.code === "ENOENT") continue;
      throw error;
    }
    if (!stat.isDirectory() || stat.isSymbolicLink()) fail5("WORK_ITEM_PACKAGE_INVALID", `${entry.id} package path is invalid`);
    await atomicWriteFile(path15.join(target, "overview.md"), renderItemOverview(entry), { fs });
    await atomicWriteFile(path15.join(target, "progress.md"), renderItemProgress(entry), { fs });
  }
}
async function withRegistry(root, fs, operation, { now } = {}) {
  await ensureRuntimeRoot(root, fs);
  return withRuntimeDirectoryTransaction(registryPath(root), async () => {
    const registry = await readRegistryUnlocked(root, fs, { allowMissing: true, now });
    return operation(registry);
  }, { fs, now });
}
async function readJsonFile(root, target, fs, code) {
  let value;
  try {
    value = JSON.parse((await readSafeRegularFile(root, target, { fs })).toString("utf8"));
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    fail5(code, `Unable to read ${path15.basename(target)}`);
  }
  return value;
}
async function readPackageDefinition(root, entry, fs) {
  const target = itemPath(root, entry.id);
  const definition = await readJsonFile(target, "baseline.json", fs, "WORK_ITEM_PACKAGE_INVALID");
  const state = await readJsonFile(target, "state.json", fs, "WORK_ITEM_PACKAGE_INVALID");
  const fingerprint2 = workItemBaselineFingerprint(definition);
  const valid = state.schemaVersion === 2 && state.id === entry.id && state.baselineFingerprint === fingerprint2 && state.contractFingerprint === workItemContractFingerprint(definition) && entry.baselineFingerprint === state.baselineFingerprint && entry.contractFingerprint === state.contractFingerprint;
  if (!valid) fail5("WORK_ITEM_PACKAGE_CHANGED", `${entry.id} package changed after preparation`, { id: entry.id });
  return { definition, state, target };
}
async function assertCurrentLineage(root, registry, entry, fs, seen = /* @__PURE__ */ new Set()) {
  if (seen.has(entry.id)) fail5("WORK_ITEM_HIERARCHY_CYCLE", "Work item hierarchy contains a cycle");
  seen.add(entry.id);
  const own = await readPackageDefinition(root, entry, fs);
  if (!entry.parentId) return own;
  const parentEntry = itemById(registry, entry.parentId);
  const parentTarget = itemPath(root, parentEntry.id);
  const parentDefinition = await readJsonFile(parentTarget, "baseline.json", fs, "WORK_ITEM_PACKAGE_INVALID");
  const actualParentContract = workItemChildContractFingerprint(parentDefinition, entry.id);
  if (entry.parentContractFingerprint !== actualParentContract || own.definition.parentContractFingerprint !== actualParentContract) {
    fail5("WORK_ITEM_BASELINE_STALE", `${entry.id} parent contract changed`, {
      id: entry.id,
      parentId: parentEntry.id,
      expected: entry.parentContractFingerprint,
      actual: actualParentContract
    });
  }
  await assertCurrentLineage(root, registry, parentEntry, fs, seen);
  return own;
}
function definitionFiles(definition, state) {
  const files = {
    "baseline.json": json6(definition),
    "baseline.md": renderWorkItemBaseline(definition),
    "work-item.json": json6({
      schemaVersion: 2,
      id: definition.id,
      kind: definition.kind,
      authorityKind: definition.authorityKind,
      parentId: definition.parentId
    }),
    "state.json": json6(state)
  };
  if (definition.children) files["children.json"] = json6({ schemaVersion: 2, children: definition.children });
  if (definition.execution) files["execution.json"] = json6({ schemaVersion: 2, ...definition.execution });
  return files;
}
async function writeNewPackage(target, files, fs) {
  await atomicWriteDirectory(target, async (staging) => {
    for (const [name, contents] of Object.entries(files)) {
      await atomicWriteFile(path15.join(staging, name), contents, { fs });
    }
  }, { fs });
}
function entryFromDefinition(definition, state, at) {
  return {
    id: definition.id,
    kind: definition.kind,
    authorityKind: definition.authorityKind,
    parentId: definition.parentId,
    childIds: definition.children?.map(({ id }) => id) ?? [],
    packagePath: itemRelativePath(definition.id),
    stage: state.stage,
    status: "PREPARED",
    baselineFingerprint: state.baselineFingerprint,
    contractFingerprint: state.contractFingerprint,
    parentContractFingerprint: state.parentContractFingerprint,
    gate: { status: "NOT_RUN", evidence: null },
    delivery: definition.kind === "DELIVERY" ? { status: "NOT_READY", review: null, userConfirmation: null } : null,
    developmentMode: null,
    claim: null,
    latestEvidence: null,
    recordRevision: 1,
    createdAt: at,
    updatedAt: at
  };
}
function validateTaskDependencies(definition, parent) {
  if (definition.kind !== "TASK") return;
  if (!parent) {
    if (definition.execution.dependsOn.length > 0) {
      fail5("WORK_ITEM_DEPENDENCY_INVALID", "A root Task cannot depend on sibling Tasks; use a Capability root");
    }
    return;
  }
  const siblingIds = new Set(parent.children.map(({ id }) => id));
  if (definition.execution.dependsOn.some((id) => !siblingIds.has(id))) {
    fail5("WORK_ITEM_DEPENDENCY_INVALID", "Task dependsOn must reference planned sibling Tasks");
  }
}
async function validateCapabilityDependencyGraph(root, registry, candidate, fs) {
  if (candidate.kind !== "CAPABILITY") return;
  const graph = /* @__PURE__ */ new Map();
  for (const entry of registry.workItems.filter(({ kind, parentId }) => kind === "CAPABILITY" && parentId === candidate.parentId)) {
    const definition = entry.id === candidate.id ? candidate : (await readPackageDefinition(root, entry, fs)).definition;
    graph.set(definition.id, definition.decomposition.dependsOn);
  }
  graph.set(candidate.id, candidate.decomposition.dependsOn);
  const visiting = /* @__PURE__ */ new Set();
  const visited = /* @__PURE__ */ new Set();
  const visit = (id) => {
    if (visiting.has(id)) fail5("WORK_ITEM_DEPENDENCY_CYCLE", "Capability dependencies contain a cycle");
    if (visited.has(id)) return;
    visiting.add(id);
    for (const dependency of graph.get(id) ?? []) if (graph.has(dependency)) visit(dependency);
    visiting.delete(id);
    visited.add(id);
  };
  for (const id of graph.keys()) visit(id);
}
async function prepareWorkItem({
  root,
  definition,
  hostRuntime: suppliedHostRuntime,
  explicitDogfood = false,
  now,
  fs = fsPromises12
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const at = timestamp4(now);
  const hostRuntime = requireHostRuntime(suppliedHostRuntime);
  return withRegistry(root, fs, async (registry) => {
    const existing = registry.workItems.find(({ id }) => id === definition?.id);
    if (existing) {
      const current = await readPackageDefinition(root, existing, fs);
      let candidate;
      if (definition.kind === "DELIVERY" || definition.parentId === null) {
        candidate = validateWorkItemDefinition(definition);
      } else {
        const parentEntry = itemById(registry, definition.parentId);
        const parent2 = (await readPackageDefinition(root, parentEntry, fs)).definition;
        candidate = validateWorkItemDefinition(definition, { parent: parent2 });
      }
      if (workItemBaselineFingerprint(candidate) !== current.state.baselineFingerprint) {
        fail5("WORK_ITEM_SOURCE_CHANGED", `${existing.id} prepared baseline differs from the requested definition`);
      }
      return { created: false, idempotent: true, id: existing.id, stage: existing.stage };
    }
    let parent = null;
    if (definition.kind !== "DELIVERY" && definition.parentId !== null) {
      const parentEntry = itemById(registry, definition.parentId);
      if (parentEntry.stage !== "BASELINE_FROZEN") fail5("WORK_ITEM_PARENT_NOT_FROZEN", "Parent baseline must be frozen first");
      parent = (await assertCurrentLineage(root, registry, parentEntry, fs)).definition;
    }
    const normalized = validateWorkItemDefinition(definition, { parent });
    validateTaskDependencies(normalized, parent);
    await validateCapabilityDependencyGraph(root, registry, normalized, fs);
    const state = {
      schemaVersion: 2,
      id: normalized.id,
      stage: "WAITING_FOR_BASELINE_CONFIRMATION",
      baselineFingerprint: workItemBaselineFingerprint(normalized),
      contractFingerprint: workItemContractFingerprint(normalized),
      parentContractFingerprint: normalized.parentContractFingerprint,
      hostRuntime,
      createdAt: at,
      frozenAt: null
    };
    const target = await assertSafePath(root, path15.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, normalized.id), { fs });
    await writeNewPackage(target, definitionFiles(normalized, state), fs);
    const entry = entryFromDefinition(normalized, state, at);
    registry.workItems.push(entry);
    if (entry.parentId) {
      const parentEntry = itemById(registry, entry.parentId);
      parentEntry.childIds = [.../* @__PURE__ */ new Set([...parentEntry.childIds, entry.id])].sort();
      parentEntry.recordRevision += 1;
      parentEntry.updatedAt = at;
    }
    registry.currentFocus = { workItemId: entry.id, purpose: "BASELINE_CONFIRMATION" };
    registry.revision += 1;
    registry.updatedAt = at;
    try {
      await writeRegistryUnlocked(root, registry, fs);
    } catch (error) {
      await fs.rm(target, { recursive: true, force: true }).catch(() => {
      });
      throw error;
    }
    return {
      created: true,
      idempotent: false,
      id: entry.id,
      kind: entry.kind,
      stage: entry.stage,
      baselineFingerprint: entry.baselineFingerprint,
      artifactDir: target
    };
  }, { now });
}
async function freezeWorkItem({
  root,
  id,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises12
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail5("CONFIRMATION_REQUIRED", "Work item baseline freeze requires explicit confirmation");
  const at = timestamp4(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (expectedBaselineFingerprint !== void 0 && entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail5("WORK_ITEM_REVISION_CONFLICT", "The confirmed baseline fingerprint is not current");
    }
    const taskPackage = await assertCurrentLineage(root, registry, entry, fs);
    if (entry.stage === "BASELINE_FROZEN") {
      return { created: false, idempotent: true, id, stage: entry.stage, baselineFingerprint: entry.baselineFingerprint };
    }
    if (entry.stage !== "WAITING_FOR_BASELINE_CONFIRMATION") fail5("WORK_ITEM_STAGE_INVALID", `${id} is not ready to freeze`);
    const state = {
      ...taskPackage.state,
      stage: "BASELINE_FROZEN",
      frozenAt: at
    };
    await atomicWriteFile(path15.join(taskPackage.target, "state.json"), json6(state), { fs });
    entry.stage = "BASELINE_FROZEN";
    entry.status = entry.kind === "TASK" ? "WAITING_FOR_DEVELOPMENT_MODE_SELECTION" : "FROZEN";
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.kind === "TASK" ? "DEVELOPMENT_MODE_SELECTION" : "DECOMPOSITION"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { created: true, idempotent: false, id, stage: entry.stage, baselineFingerprint: entry.baselineFingerprint };
  }, { now });
}
async function retryBlockedWorkItem({
  root,
  id,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises12
}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail5("CONFIRMATION_REQUIRED", "Work item retry requires explicit confirmation");
  const at = timestamp4(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.status !== "BLOCKED" || entry.claim) {
      fail5("WORK_ITEM_RETRY_INVALID", "Only an unclaimed BLOCKED work item can be retried");
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail5("WORK_ITEM_REVISION_CONFLICT", "The retry baseline fingerprint is not current");
    }
    await assertCurrentLineage(root, registry, entry, fs);
    entry.status = "FROZEN";
    entry.gate = { status: "NOT_RUN", evidence: null };
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.kind === "TASK" ? "EXECUTION_RETRY" : "AGGREGATE_GATE_RETRY"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status: entry.status, baselineFingerprint: entry.baselineFingerprint };
  }, { now });
}
async function retryWorkItem(options = {}) {
  return retryBlockedWorkItem(options);
}
async function copyPackageContents(source, staging, fs) {
  const entries = await fs.readdir(source, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isSymbolicLink()) fail5("WORK_ITEM_PACKAGE_INVALID", "Work item packages cannot contain symbolic links");
    await fs.cp(path15.join(source, entry.name), path15.join(staging, entry.name), {
      recursive: entry.isDirectory(),
      force: false,
      errorOnExist: true
    });
  }
}
async function reviseWorkItem({
  root,
  definition,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises12
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) fail5("CONFIRMATION_REQUIRED", "Work item baseline revision requires explicit confirmation");
  const at = timestamp4(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, definition?.id);
    if (entry.stage !== "BASELINE_FROZEN") fail5("WORK_ITEM_STAGE_INVALID", "Only frozen work items can be revised");
    if (entry.status === "VERIFIED") fail5("WORK_ITEM_REVISION_AFTER_VERIFICATION", "Verified work items cannot be revised");
    if (entry.status === "BLOCKED") {
      fail5("WORK_ITEM_RETRY_REQUIRED", "A BLOCKED work item must be explicitly retried before baseline revision");
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail5("WORK_ITEM_REVISION_CONFLICT", "The expected baseline fingerprint is not current");
    }
    const current = await assertCurrentLineage(root, registry, entry, fs);
    let parent;
    if (entry.parentId) {
      const parentEntry = itemById(registry, entry.parentId);
      parent = (await assertCurrentLineage(root, registry, parentEntry, fs)).definition;
    }
    const normalized = validateWorkItemDefinition(definition, { parent });
    if (normalized.id !== entry.id || normalized.kind !== entry.kind) {
      fail5("WORK_ITEM_REVISION_IDENTITY_CHANGED", "A revision cannot change work item identity or kind");
    }
    if (current.definition.children) {
      const revisedIds = new Set(normalized.children.map(({ id }) => id));
      const removed = current.definition.children.filter(({ id }) => !revisedIds.has(id));
      if (removed.length > 0) fail5("WORK_ITEM_CHILD_REMOVAL_FORBIDDEN", "Baseline revisions may append or refine children but cannot remove them");
    }
    const activeDescendants = registry.workItems.filter((candidate) => candidate.claim && isDescendantOf(registry, candidate, entry.id));
    if (entry.kind === "TASK" && activeDescendants.length > 0) {
      fail5("WORK_ITEM_REVISION_ACTIVE_CLAIM", "A claimed Task cannot be revised");
    }
    for (const candidate of activeDescendants) {
      let directChild = candidate;
      while (directChild.parentId && directChild.parentId !== entry.id) {
        directChild = itemById(registry, directChild.parentId);
      }
      const before = workItemChildContractFingerprint(current.definition, directChild.id);
      const after = workItemChildContractFingerprint(normalized, directChild.id);
      if (before !== after) {
        fail5("WORK_ITEM_REVISION_ACTIVE_CLAIM", "A revision cannot invalidate an actively claimed descendant");
      }
    }
    validateTaskDependencies(normalized, parent);
    await validateCapabilityDependencyGraph(root, registry, normalized, fs);
    const state = {
      ...current.state,
      baselineFingerprint: workItemBaselineFingerprint(normalized),
      contractFingerprint: workItemContractFingerprint(normalized),
      parentContractFingerprint: normalized.parentContractFingerprint,
      baselineRevision: (current.state.baselineRevision ?? 1) + 1,
      revisedAt: at
    };
    const files = definitionFiles(normalized, state);
    await atomicReplaceDirectory(current.target, async (staging) => {
      await copyPackageContents(current.target, staging, fs);
      for (const [name, contents] of Object.entries(files)) {
        await atomicWriteFile(path15.join(staging, name), contents, { fs });
      }
      if (entry.kind === "TASK") {
        for (const name of ["development-mode.json", "context-manifest.json", "development-handoff.md"]) {
          await fs.rm(path15.join(staging, name), { force: true });
        }
      }
    }, { fs });
    entry.childIds = normalized.children?.map(({ id }) => id) ?? [];
    entry.baselineFingerprint = state.baselineFingerprint;
    entry.contractFingerprint = state.contractFingerprint;
    entry.parentContractFingerprint = state.parentContractFingerprint;
    entry.status = entry.kind === "TASK" ? "WAITING_FOR_DEVELOPMENT_MODE_SELECTION" : "FROZEN";
    entry.developmentMode = null;
    entry.gate = { status: "NOT_RUN", evidence: null };
    entry.latestEvidence = null;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: entry.id,
      purpose: entry.kind === "TASK" ? "DEVELOPMENT_MODE_SELECTION" : "DECOMPOSITION"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return {
      id: entry.id,
      kind: entry.kind,
      baselineRevision: state.baselineRevision,
      baselineFingerprint: state.baselineFingerprint,
      status: entry.status
    };
  }, { now });
}
async function selectDevelopmentMode({
  root,
  id,
  mode,
  expectedBaselineFingerprint,
  confirmed = false,
  explicitDogfood = false,
  now,
  fs = fsPromises12
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (confirmed !== true) {
    fail5("CONFIRMATION_REQUIRED", "Development mode selection requires explicit user confirmation");
  }
  if (!DEVELOPMENT_MODES.includes(mode)) {
    fail5("WORK_ITEM_DEVELOPMENT_MODE_INVALID", "Development mode must be active or manual");
  }
  const at = timestamp4(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind !== "TASK" || entry.stage !== "BASELINE_FROZEN") {
      fail5("WORK_ITEM_TASK_REQUIRED", "Development mode can only be selected for a frozen Task");
    }
    if (entry.baselineFingerprint !== expectedBaselineFingerprint) {
      fail5("WORK_ITEM_REVISION_CONFLICT", "The development mode confirmation is not bound to the current baseline");
    }
    if (entry.claim || !["WAITING_FOR_DEVELOPMENT_MODE_SELECTION", "FROZEN"].includes(entry.status)) {
      fail5("WORK_ITEM_DEVELOPMENT_MODE_LOCKED", "Development mode cannot change after Task dispatch begins");
    }
    if (entry.developmentMode?.mode === mode) {
      return {
        created: false,
        idempotent: true,
        id,
        status: entry.status,
        developmentMode: entry.developmentMode
      };
    }
    if (entry.developmentMode !== null) {
      fail5("WORK_ITEM_DEVELOPMENT_MODE_LOCKED", "Development mode is fixed for the current Task baseline");
    }
    const record = {
      schemaVersion: 1,
      taskId: id,
      baselineFingerprint: entry.baselineFingerprint,
      mode,
      confirmedBy: "user",
      confirmedAt: at
    };
    const target = itemPath(root, id);
    await atomicWriteFile(path15.join(target, "development-mode.json"), json6(record), { fs });
    entry.developmentMode = record;
    entry.status = "FROZEN";
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: mode === "active" ? "ACTIVE_DISPATCH" : "MANUAL_HANDOFF"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    try {
      await writeRegistryUnlocked(root, registry, fs);
    } catch (error) {
      await fs.rm(path15.join(target, "development-mode.json"), { force: true });
      throw error;
    }
    return {
      created: true,
      idempotent: false,
      id,
      status: entry.status,
      developmentMode: record
    };
  }, { now });
}
function isDescendantOf(registry, entry, ancestorId) {
  let current = entry;
  const visited = /* @__PURE__ */ new Set();
  while (current) {
    if (current.id === ancestorId) return true;
    if (!current.parentId || visited.has(current.id)) return false;
    visited.add(current.id);
    current = registry.workItems.find(({ id }) => id === current.parentId);
  }
  return false;
}
async function taskDefinition(root, entry, fs) {
  return (await readPackageDefinition(root, entry, fs)).definition;
}
async function taskReady(root, registry, entry, fs) {
  if (entry.kind !== "TASK" || entry.stage !== "BASELINE_FROZEN" || entry.status !== "FROZEN" || entry.claim) return false;
  await assertCurrentLineage(root, registry, entry, fs);
  const definition = await taskDefinition(root, entry, fs);
  let capabilitiesReady = true;
  if (entry.parentId !== null) {
    const capabilityEntry = itemById(registry, entry.parentId);
    const capability = (await readPackageDefinition(root, capabilityEntry, fs)).definition;
    capabilitiesReady = capability.decomposition.dependsOn.every((id) => registry.workItems.find((candidate) => candidate.id === id)?.status === "VERIFIED");
  }
  if (!capabilitiesReady) return false;
  const dependenciesReady = definition.execution.dependsOn.every((id) => registry.workItems.find((candidate) => candidate.id === id)?.status === "VERIFIED");
  if (!dependenciesReady) return false;
  for (const claimed of registry.workItems.filter((candidate) => candidate.claim)) {
    const claimedDefinition = await taskDefinition(root, claimed, fs);
    if (scopePatternsOverlap(definition.scope, claimedDefinition.scope)) return false;
  }
  return true;
}
async function listReadyTasks({ root, workItemId, fs = fsPromises12 } = {}) {
  const registry = await readRegistryUnlocked(root, fs);
  itemById(registry, workItemId);
  const ready = [];
  for (const entry of sortedItems(registry.workItems)) {
    if (isDescendantOf(registry, entry, workItemId) && await taskReady(root, registry, entry, fs)) ready.push(entry.id);
  }
  return ready;
}
function safeOperationId(value, field) {
  if (typeof value !== "string" || !/^[a-z0-9][a-z0-9._-]*$/.test(value)) {
    fail5("WORK_ITEM_OPERATION_INVALID", `${field} must be a safe lowercase identifier`);
  }
  return value;
}
async function claimTask({ root, id, owner, operationId, explicitDogfood = false, now, fs = fsPromises12 } = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const at = timestamp4(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind === "TASK" && entry.developmentMode === null) {
      fail5("WORK_ITEM_DEVELOPMENT_MODE_REQUIRED", `${id} requires an explicitly confirmed development mode`);
    }
    if (!await taskReady(root, registry, entry, fs)) fail5("WORK_ITEM_NOT_READY", `${id} is not ready for dispatch`);
    entry.claim = {
      owner: safeOperationId(owner, "owner"),
      operationId: safeOperationId(operationId, "operationId"),
      claimedAt: at
    };
    entry.status = "CLAIMED";
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = { workItemId: id, purpose: "EXECUTION" };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status: entry.status, claim: entry.claim };
  }, { now });
}
function evidenceRecord(value) {
  const valid = value && typeof value === "object" && !Array.isArray(value) && typeof value.path === "string" && value.path.length > 0 && !path15.posix.isAbsolute(value.path.replaceAll("\\", "/")) && !value.path.replaceAll("\\", "/").split("/").includes("..") && typeof value.sha256 === "string" && /^[a-f0-9]{64}$/.test(value.sha256);
  if (!valid) fail5("WORK_ITEM_EVIDENCE_INVALID", "Evidence must contain a safe relative path and sha256");
  return { path: value.path.replaceAll("\\", "/"), sha256: value.sha256 };
}
async function verifiedDeliveryEvidence(root, evidence, action, fs) {
  const reference = evidenceRecord(evidence);
  let bytes;
  try {
    bytes = await readSafeRegularFile(root, reference.path, { fs });
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    fail5("WORK_ITEM_DELIVERY_EVIDENCE_MISSING", `Unable to read delivery evidence: ${reference.path}`);
  }
  if (sha256Bytes(bytes) !== reference.sha256) {
    fail5("WORK_ITEM_DELIVERY_EVIDENCE_CHANGED", `Delivery evidence hash does not match: ${reference.path}`);
  }
  let artifact;
  try {
    artifact = JSON.parse(bytes.toString("utf8"));
  } catch {
    fail5("WORK_ITEM_DELIVERY_EVIDENCE_INVALID", "Delivery evidence must be valid JSON");
  }
  if (!validDeliveryArtifact(action, artifact)) {
    fail5("WORK_ITEM_DELIVERY_EVIDENCE_INVALID", `Delivery evidence does not prove ${action}`);
  }
  return { reference, artifact };
}
async function recordTaskResult({
  root,
  id,
  operationId,
  status,
  evidence,
  explicitDogfood = false,
  now,
  fs = fsPromises12
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!["IMPLEMENTED", "BLOCKED"].includes(status)) fail5("WORK_ITEM_RESULT_INVALID", "Task result must be IMPLEMENTED or BLOCKED");
  const at = timestamp4(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind !== "TASK" || entry.status !== "CLAIMED" || entry.claim?.operationId !== operationId) {
      fail5("WORK_ITEM_OPERATION_INVALID", `${id} does not have the supplied active operation`);
    }
    entry.status = status;
    entry.claim = null;
    entry.latestEvidence = evidenceRecord(evidence);
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status };
  }, { now });
}
function allChildrenVerified(registry, entry, definition) {
  const actual = new Map(registry.workItems.filter(({ parentId }) => parentId === entry.id).map((item) => [item.id, item]));
  return definition.children.length > 0 && definition.children.every(({ id }) => actual.get(id)?.status === "VERIFIED");
}
async function recordWorkItemGate({ root, id, status, evidence, explicitDogfood = false, now, fs = fsPromises12 } = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!["PASS", "FAIL"].includes(status)) fail5("WORK_ITEM_GATE_INVALID", "Gate status must be PASS or FAIL");
  const at = timestamp4(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    const taskPackage = await assertCurrentLineage(root, registry, entry, fs);
    if (entry.status === "BLOCKED") {
      fail5("WORK_ITEM_RETRY_REQUIRED", `${id} must be explicitly retried before its gate can run again`);
    }
    if (entry.status === "VERIFIED") {
      fail5("WORK_ITEM_GATE_ALREADY_PASSED", `${id} gate has already passed`);
    }
    if (status === "PASS") {
      if (entry.kind === "TASK" && entry.status !== "IMPLEMENTED") {
        fail5("WORK_ITEM_IMPLEMENTATION_INCOMPLETE", `${id} must be implemented before its gate can pass`);
      }
      if (entry.kind !== "TASK") {
        if (taskPackage.definition.decomposition.status !== "SEALED") {
          fail5("WORK_ITEM_DECOMPOSITION_OPEN", `${id} decomposition must be SEALED before its aggregate gate can pass`);
        }
        if (!allChildrenVerified(registry, entry, taskPackage.definition)) {
          fail5("WORK_ITEM_CHILDREN_INCOMPLETE", `${id} children must all be verified before its aggregate gate can pass`);
        }
      }
    }
    entry.gate = { status, evidence: evidenceRecord(evidence) };
    entry.status = status === "PASS" ? "VERIFIED" : "BLOCKED";
    if (entry.kind === "DELIVERY") {
      entry.delivery = status === "PASS" ? { status: "WAITING_FOR_INDEPENDENT_REVIEW", review: null, userConfirmation: null } : { status: "NOT_READY", review: null, userConfirmation: null };
    }
    entry.latestEvidence = entry.gate.evidence;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = { workItemId: id, purpose: status === "PASS" ? "AGGREGATION" : "BLOCKER" };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, status: entry.status, gate: entry.gate };
  }, { now });
}
async function recordDelivery({
  root,
  id,
  action,
  evidence,
  explicitDogfood = false,
  now,
  fs = fsPromises12
} = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  if (!["INDEPENDENT_REVIEW_PASS", "HUMAN_REVIEW_ACCEPTED", "USER_CONFIRMED"].includes(action)) {
    fail5("WORK_ITEM_DELIVERY_ACTION_INVALID", "Delivery action is invalid");
  }
  const at = timestamp4(now);
  return withRegistry(root, fs, async (registry) => {
    const entry = itemById(registry, id);
    if (entry.kind !== "DELIVERY" || entry.status !== "VERIFIED") {
      fail5("WORK_ITEM_DELIVERY_INVALID", "Only a verified Delivery can advance delivery");
    }
    await assertCurrentLineage(root, registry, entry, fs);
    entry.delivery ??= {
      status: "WAITING_FOR_INDEPENDENT_REVIEW",
      review: null,
      userConfirmation: null
    };
    if (action === "USER_CONFIRMED") {
      if (entry.delivery.status !== "WAITING_FOR_USER_CONFIRMATION") {
        fail5("WORK_ITEM_DELIVERY_STAGE_INVALID", "User confirmation requires a passed independent or accepted human review");
      }
      const verifiedEvidence2 = await verifiedDeliveryEvidence(root, evidence, action, fs);
      const reviewEvidence = entry.delivery.review.evidence;
      if (reviewEvidence.path === verifiedEvidence2.reference.path || reviewEvidence.sha256 === verifiedEvidence2.reference.sha256) {
        fail5("WORK_ITEM_DELIVERY_EVIDENCE_REUSED", "User confirmation evidence must be distinct from review evidence");
      }
      entry.delivery = {
        ...entry.delivery,
        status: "COMPLETED",
        userConfirmation: {
          action,
          evidence: verifiedEvidence2.reference,
          artifact: verifiedEvidence2.artifact,
          recordedAt: at
        }
      };
    } else {
      if (entry.delivery.status !== "WAITING_FOR_INDEPENDENT_REVIEW") {
        fail5("WORK_ITEM_DELIVERY_STAGE_INVALID", "Delivery is not waiting for independent review");
      }
      const verifiedEvidence2 = await verifiedDeliveryEvidence(root, evidence, action, fs);
      entry.delivery = {
        ...entry.delivery,
        status: "WAITING_FOR_USER_CONFIRMATION",
        review: {
          action,
          evidence: verifiedEvidence2.reference,
          artifact: verifiedEvidence2.artifact,
          recordedAt: at
        }
      };
    }
    entry.latestEvidence = action === "USER_CONFIRMED" ? entry.delivery.userConfirmation.evidence : entry.delivery.review.evidence;
    entry.recordRevision += 1;
    entry.updatedAt = at;
    registry.currentFocus = {
      workItemId: id,
      purpose: entry.delivery.status === "COMPLETED" ? "DELIVERY_COMPLETE" : "USER_CONFIRMATION"
    };
    registry.revision += 1;
    registry.updatedAt = at;
    await writeRegistryUnlocked(root, registry, fs);
    return { id, action, delivery: entry.delivery };
  }, { now });
}
function parentContractSnapshot(parent, childId) {
  const child = parent.children.find(({ id }) => id === childId);
  return {
    id: parent.id,
    kind: parent.kind,
    contractFingerprint: workItemChildContractFingerprint(parent, childId),
    goal: parent.goal,
    scope: parent.scope,
    childContract: child
  };
}
function renderTaskHandoff(context) {
  return [
    "# Task Development Handoff",
    "",
    `Task: ${context.task.id}`,
    `Development mode: ${context.developmentMode}`,
    "Frozen authority: `baseline.json`",
    "Independent context: `context-manifest.json`",
    "",
    "## Rules",
    "- Implement only this frozen leaf Task.",
    "- Do not reinterpret parent contracts or acceptance criteria.",
    "- Write only within the listed Scope.",
    "- Return BLOCKED when a dependency, contract, or workspace is unavailable.",
    "- Do not commit, push, publish, or report PASS.",
    "",
    "## Scope",
    ...context.task.scope.map((entry) => `- ${entry}`),
    "",
    "## Test Commands",
    ...context.testCommands.map((argv) => `- ${JSON.stringify(argv)}`),
    ""
  ].join("\n");
}
async function buildTaskContext({ root, id, explicitDogfood = false, fs = fsPromises12 } = {}) {
  await assertSelfHostingDogfood(root, explicitDogfood, fs);
  const registry = await readRegistryUnlocked(root, fs);
  const entry = itemById(registry, id);
  if (entry.kind !== "TASK" || entry.stage !== "BASELINE_FROZEN") {
    fail5("WORK_ITEM_TASK_REQUIRED", "Independent context can only be built for a frozen Task");
  }
  if (entry.developmentMode === null) {
    fail5("WORK_ITEM_DEVELOPMENT_MODE_REQUIRED", `${id} requires an explicitly confirmed development mode`);
  }
  const own = await assertCurrentLineage(root, registry, entry, fs);
  const parents = [];
  let childId = entry.id;
  let parentId = entry.parentId;
  while (parentId) {
    const parentEntry = itemById(registry, parentId);
    const parent = (await readPackageDefinition(root, parentEntry, fs)).definition;
    parents.unshift(parentContractSnapshot(parent, childId));
    childId = parent.id;
    parentId = parent.parentId;
  }
  const dependencies = [];
  for (const dependencyId of own.definition.execution.dependsOn) {
    const dependency = itemById(registry, dependencyId);
    const definition = (await readPackageDefinition(root, dependency, fs)).definition;
    dependencies.push({
      id: dependency.id,
      status: dependency.status,
      outputs: definition.execution.outputs,
      evidence: dependency.latestEvidence
    });
  }
  if (dependencies.some(({ status }) => status !== "VERIFIED")) {
    fail5("WORK_ITEM_NOT_READY", `${id} has unverified Task dependencies`);
  }
  let capabilityDependencies = [];
  if (entry.parentId !== null) {
    const capabilityEntry = itemById(registry, entry.parentId);
    const capabilityDefinition = (await readPackageDefinition(root, capabilityEntry, fs)).definition;
    capabilityDependencies = capabilityDefinition.decomposition.dependsOn.map((dependencyId) => {
      const dependency = itemById(registry, dependencyId);
      return {
        id: dependency.id,
        status: dependency.status,
        contractFingerprint: dependency.contractFingerprint,
        evidence: dependency.latestEvidence
      };
    });
  }
  if (capabilityDependencies.some(({ status }) => status !== "VERIFIED")) {
    fail5("WORK_ITEM_NOT_READY", `${id} has unverified Capability dependencies`);
  }
  const context = {
    schemaVersion: 1,
    developmentMode: entry.developmentMode.mode,
    task: {
      id: own.definition.id,
      title: own.definition.title,
      goal: own.definition.goal,
      scope: own.definition.scope,
      baselineFingerprint: entry.baselineFingerprint
    },
    parentContracts: parents,
    capabilityDependencies,
    dependencies,
    requirements: own.definition.requirements,
    acceptance: own.definition.acceptance,
    execution: own.definition.execution,
    testCommands: own.definition.testCommands,
    rules: {
      inheritConversation: false,
      allowRequirementChanges: false,
      allowExternalStateChanges: false
    }
  };
  await atomicWriteFile(path15.join(own.target, "context-manifest.json"), json6(context), { fs });
  await atomicWriteFile(path15.join(own.target, "development-handoff.md"), renderTaskHandoff(context), { fs });
  return context;
}

// src/cli/main.mjs
var COMMANDS = Object.freeze([
  "route",
  "start",
  "prepare",
  "freeze",
  "self-check",
  "accept",
  "prepare-item",
  "freeze-item",
  "revise-item",
  "select-development-mode",
  "ready-tasks",
  "task-context",
  "claim-task",
  "task-result",
  "retry-item",
  "gate-item",
  "delivery-item"
]);
var HIERARCHICAL_COMMANDS = Object.freeze([
  "prepare-item",
  "freeze-item",
  "revise-item",
  "select-development-mode",
  "ready-tasks",
  "task-context",
  "claim-task",
  "task-result",
  "retry-item",
  "gate-item",
  "delivery-item"
]);
var VALUE_OPTIONS = /* @__PURE__ */ new Set([
  "--mode",
  "--signals",
  "--task",
  "--brief",
  "--host-runtime",
  "--baseline",
  "--source",
  "--round",
  "--snapshot",
  "--timeout-ms",
  "--reviewer",
  "--review-result",
  "--definition",
  "--item",
  "--owner",
  "--operation",
  "--status",
  "--evidence",
  "--expected-baseline",
  "--action",
  "--development-mode"
]);
var REPEATABLE_OPTIONS = /* @__PURE__ */ new Set(["--source"]);
var FLAG_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--confirmed", "--dogfood"]);
var ROUTE_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--mode", "--signals"]);
var START_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--mode", "--signals", "--task", "--brief", "--host-runtime", "--confirmed", "--dogfood"]);
var PREPARE_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--task", "--baseline", "--source", "--dogfood"]);
var FREEZE_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--task", "--confirmed", "--dogfood"]);
var SELF_CHECK_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--task", "--round", "--snapshot", "--timeout-ms", "--dogfood"]);
var ACCEPT_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--task", "--round", "--snapshot", "--timeout-ms", "--reviewer", "--review-result", "--dogfood"]);
var PREPARE_ITEM_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--definition", "--host-runtime", "--dogfood"]);
var FREEZE_ITEM_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--item", "--expected-baseline", "--confirmed", "--dogfood"]);
var REVISE_ITEM_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--definition", "--expected-baseline", "--confirmed", "--dogfood"]);
var READY_TASKS_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--item"]);
var TASK_CONTEXT_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--item", "--dogfood"]);
var SELECT_DEVELOPMENT_MODE_OPTIONS = /* @__PURE__ */ new Set([
  "--json",
  "--help",
  "--item",
  "--development-mode",
  "--expected-baseline",
  "--confirmed",
  "--dogfood"
]);
var CLAIM_TASK_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--item", "--owner", "--operation", "--dogfood"]);
var TASK_RESULT_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--item", "--operation", "--status", "--evidence", "--dogfood"]);
var GATE_ITEM_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--item", "--status", "--evidence", "--dogfood"]);
var RETRY_ITEM_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--item", "--expected-baseline", "--confirmed", "--dogfood"]);
var DELIVERY_ITEM_OPTIONS = /* @__PURE__ */ new Set(["--json", "--help", "--item", "--action", "--evidence", "--dogfood"]);
var hierarchicalUsage = `  prepare-item --definition <file> --host-runtime <agent>
  freeze-item --item <id> --expected-baseline <sha256> --confirmed
  revise-item --definition <file> --expected-baseline <sha256> --confirmed
  select-development-mode --item <task-id> --development-mode active|manual --expected-baseline <sha256> --confirmed
  ready-tasks --item <root-or-subtree-id>
  task-context --item <task-id>
  claim-task --item <task-id> --owner <owner> --operation <id>
  task-result --item <task-id> --operation <id> --status IMPLEMENTED|BLOCKED --evidence <file>
  retry-item --item <id> --expected-baseline <sha256> --confirmed
  gate-item --item <id> --status PASS|FAIL --evidence <file>
  delivery-item --item <delivery-id> --action INDEPENDENT_REVIEW_PASS|HUMAN_REVIEW_ACCEPTED|USER_CONFIRMED --evidence <file>`;
var help = `Usage: gated-loop <command> [options]

Commands:
${COMMANDS.map((command) => `  ${command}`).join("\n")}

Hierarchical work items:
${hierarchicalUsage}

In the implementation repository, add --dogfood to every hierarchical command that writes runtime state.

Gate commands:
  self-check --task <id> [--round 1] [--snapshot <file>]
  accept --task <id> [--round 1] [--reviewer human|auto|codex|claude]

The historical start/prepare/freeze commands are v1 compatibility surfaces.
Acceptance defaults to human handling unless a host reviewer result or reviewer capability is supplied.
`;
var hierarchicalHelp = `Usage: hdg <command> [options]

Commands:
${HIERARCHICAL_COMMANDS.map((command) => `  ${command}`).join("\n")}

${hierarchicalUsage}

In the hierarchical-delivery-governance implementation repository, every command that writes control state also requires --dogfood.
`;
function parse2(argv) {
  const seen = /* @__PURE__ */ new Set();
  const values = {};
  const positionals = [];
  for (let index = 0; index < argv.length; index++) {
    const item = argv[index];
    if (!item.startsWith("--")) {
      positionals.push(item);
      continue;
    }
    if (!VALUE_OPTIONS.has(item) && !FLAG_OPTIONS.has(item)) throw new GatedLoopError("UNKNOWN_OPTION", `Unknown option: ${item}`);
    if (seen.has(item) && !REPEATABLE_OPTIONS.has(item)) throw new GatedLoopError("DUPLICATE_OPTION", `Duplicate option: ${item}`);
    seen.add(item);
    if (VALUE_OPTIONS.has(item)) {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) throw new GatedLoopError("OPTION_VALUE_REQUIRED", `Missing value for option: ${item}`);
      if (REPEATABLE_OPTIONS.has(item)) (values[item] ??= []).push(value);
      else values[item] = value;
      index++;
    }
  }
  const [command, ...extraPositionals] = positionals;
  const acceptsDescription = command === "route" || command === "start";
  if (extraPositionals.length > (acceptsDescription ? 1 : 0)) {
    throw new GatedLoopError("UNKNOWN_OPTION", `Unexpected positional argument: ${extraPositionals.at(-1)}`);
  }
  const commandOptions = {
    route: ROUTE_OPTIONS,
    start: START_OPTIONS,
    prepare: PREPARE_OPTIONS,
    freeze: FREEZE_OPTIONS,
    "self-check": SELF_CHECK_OPTIONS,
    accept: ACCEPT_OPTIONS,
    "prepare-item": PREPARE_ITEM_OPTIONS,
    "freeze-item": FREEZE_ITEM_OPTIONS,
    "revise-item": REVISE_ITEM_OPTIONS,
    "ready-tasks": READY_TASKS_OPTIONS,
    "task-context": TASK_CONTEXT_OPTIONS,
    "select-development-mode": SELECT_DEVELOPMENT_MODE_OPTIONS,
    "claim-task": CLAIM_TASK_OPTIONS,
    "task-result": TASK_RESULT_OPTIONS,
    "retry-item": RETRY_ITEM_OPTIONS,
    "gate-item": GATE_ITEM_OPTIONS,
    "delivery-item": DELIVERY_ITEM_OPTIONS
  };
  if (commandOptions[command]) {
    for (const option of seen) if (!commandOptions[command].has(option)) {
      throw new GatedLoopError("UNKNOWN_OPTION", `Option is not valid for ${command}: ${option}`);
    }
  }
  if (values["--mode"] !== void 0 && !["full", "light"].includes(values["--mode"])) {
    throw new GatedLoopError("OPTION_VALUE_INVALID", "--mode must be full or light");
  }
  if (values["--host-runtime"] !== void 0 && !isAgentRuntime(values["--host-runtime"])) {
    throw new GatedLoopError("OPTION_VALUE_INVALID", "--host-runtime must be a safe lowercase Agent identifier");
  }
  if (values["--development-mode"] !== void 0 && !["active", "manual"].includes(values["--development-mode"])) {
    throw new GatedLoopError("OPTION_VALUE_INVALID", "--development-mode must be active or manual");
  }
  if (values["--reviewer"] !== void 0 && !["human", "auto", "codex", "claude"].includes(values["--reviewer"])) {
    throw new GatedLoopError("OPTION_VALUE_INVALID", "--reviewer must be human, auto, codex, or claude");
  }
  if (values["--timeout-ms"] !== void 0 && (!/^\d+$/.test(values["--timeout-ms"]) || Number(values["--timeout-ms"]) < 1)) {
    throw new GatedLoopError("OPTION_VALUE_INVALID", "--timeout-ms must be a positive integer");
  }
  return {
    command,
    description: extraPositionals[0],
    json: seen.has("--json"),
    help: seen.has("--help"),
    confirmed: seen.has("--confirmed"),
    dogfood: seen.has("--dogfood"),
    values
  };
}
async function readStructured(source, kind, { cwd, fs, stdin }) {
  let text2;
  try {
    if (source === "-") {
      if (stdin !== void 0) text2 = typeof stdin === "function" ? await stdin() : stdin;
      else text2 = await fs.readFile(0, "utf8");
    } else {
      const portable = String(source).replaceAll("\\", "/").toLowerCase();
      const basename = portable.split("/").at(-1);
      if (basename.startsWith(".env") || portable.includes("production")) {
        throw new GatedLoopError("INPUT_PATH_FORBIDDEN", "Structured input path is forbidden");
      }
      text2 = (await readSafeRegularFile(cwd, source, { fs })).toString("utf8");
    }
  } catch (error) {
    if (error instanceof GatedLoopError) throw error;
    throw new GatedLoopError(`${kind}_READ`, `Unable to read ${kind.toLowerCase()} JSON`);
  }
  try {
    const value = JSON.parse(String(text2));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("not a mapping");
    return value;
  } catch {
    throw new GatedLoopError(`${kind}_PARSE`, `${kind.toLowerCase()} JSON must be a mapping`);
  }
}
async function runModeCommand(parsed, io) {
  if (typeof parsed.description !== "string" || parsed.description.trim().length === 0) {
    throw new GatedLoopError("DESCRIPTION_REQUIRED", `${parsed.command} requires a task description`);
  }
  const cwd = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises13;
  if (parsed.command === "start" && parsed.values["--signals"] === "-" && parsed.values["--brief"] === "-") {
    throw new GatedLoopError("INPUT_STDIN_CONFLICT", "Signals and brief cannot both use stdin");
  }
  const supplied = parsed.values["--signals"] ? await readStructured(parsed.values["--signals"], "MODE_INPUT", { cwd, fs, stdin: io.stdin }) : {};
  const signals = { ...supplied, description: parsed.description };
  if (parsed.values["--mode"]) signals.requestedMode = parsed.values["--mode"];
  if (parsed.command === "route") return routeTask(signals);
  const brief = parsed.values["--brief"] ? await readStructured(parsed.values["--brief"], "LIGHT_BRIEF", { cwd, fs, stdin: io.stdin }) : void 0;
  return startTask({
    root: cwd,
    task: parsed.values["--task"],
    signals,
    brief,
    confirmed: parsed.confirmed,
    explicitDogfood: parsed.dogfood,
    hostRuntime: parsed.values["--host-runtime"],
    generateTaskId: io.generateTaskId,
    now: io.now,
    beforeCommit: io.beforeCommit,
    fs
  });
}
function required(parsed, option) {
  const value = parsed.values[option];
  if (value === void 0) throw new GatedLoopError("OPTION_REQUIRED", `${parsed.command} requires ${option}`);
  return value;
}
async function assertCliMutationAllowed(root, explicitDogfood, fs) {
  let packageName;
  try {
    const packageJson = JSON.parse((await readSafeRegularFile(root, "package.json", { fs })).toString("utf8"));
    if (typeof packageJson?.name === "string") packageName = packageJson.name;
  } catch (error) {
    if (error?.code !== "ENOENT" && error?.code !== "PATH_MISSING" && !(error instanceof SyntaxError)) throw error;
  }
  const policy = resolveSelfHostingPolicy({ packageName, explicitDogfood });
  if (policy.createsRuntimePackage === false) {
    throw new GatedLoopError(
      "SELF_HOSTING_DOGFOOD_REQUIRED",
      "The hierarchical governance implementation repository requires explicit dogfood for runtime mutations"
    );
  }
}
async function runBaselineCommand(parsed, io) {
  const root = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises13;
  await assertCliMutationAllowed(root, parsed.dogfood, fs);
  const task = required(parsed, "--task");
  if (parsed.command === "prepare") {
    return prepareFullBaseline({
      root,
      task,
      baseline: required(parsed, "--baseline"),
      sources: parsed.values["--source"] ?? [],
      now: io.now,
      beforeCommit: io.beforeCommit,
      fs
    });
  }
  return freezeFullBaseline({
    root,
    task,
    confirmed: parsed.confirmed,
    now: io.now,
    beforeCommit: io.beforeCommit,
    fs
  });
}
async function runGateCommand(parsed, io) {
  const root = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises13;
  await assertCliMutationAllowed(root, parsed.dogfood, fs);
  const common = {
    root,
    task: required(parsed, "--task"),
    round: parsed.values["--round"],
    snapshot: parsed.values["--snapshot"],
    timeoutMs: parsed.values["--timeout-ms"] ? Number(parsed.values["--timeout-ms"]) : void 0,
    fs,
    runProcessImpl: io.runProcess,
    now: io.now
  };
  if (parsed.command === "self-check") return runSelfCheck(common);
  const reviewResult = parsed.values["--review-result"] ? await readStructured(parsed.values["--review-result"], "REVIEW_RESULT", { cwd: root, fs, stdin: io.stdin }) : void 0;
  return runAcceptance({
    ...common,
    reviewer: parsed.values["--reviewer"],
    reviewResult,
    reviewerInvoker: io.reviewerInvoker
  });
}
async function runWorkItemCommand(parsed, io) {
  const root = io.cwd ?? process.cwd();
  const fs = io.fs ?? fsPromises13;
  const common = { root, fs, now: io.now, explicitDogfood: parsed.dogfood };
  if (parsed.command === "prepare-item") {
    const definition = await readStructured(
      required(parsed, "--definition"),
      "WORK_ITEM_DEFINITION",
      { cwd: root, fs, stdin: io.stdin }
    );
    return prepareWorkItem({
      ...common,
      definition,
      hostRuntime: required(parsed, "--host-runtime")
    });
  }
  if (parsed.command === "freeze-item") {
    return freezeWorkItem({
      ...common,
      id: required(parsed, "--item"),
      expectedBaselineFingerprint: required(parsed, "--expected-baseline"),
      confirmed: parsed.confirmed
    });
  }
  if (parsed.command === "revise-item") {
    const definition = await readStructured(
      required(parsed, "--definition"),
      "WORK_ITEM_DEFINITION",
      { cwd: root, fs, stdin: io.stdin }
    );
    return reviseWorkItem({
      ...common,
      definition,
      expectedBaselineFingerprint: required(parsed, "--expected-baseline"),
      confirmed: parsed.confirmed
    });
  }
  if (parsed.command === "ready-tasks") {
    return listReadyTasks({ ...common, workItemId: required(parsed, "--item") });
  }
  if (parsed.command === "task-context") {
    return buildTaskContext({ ...common, id: required(parsed, "--item") });
  }
  if (parsed.command === "select-development-mode") {
    return selectDevelopmentMode({
      ...common,
      id: required(parsed, "--item"),
      mode: required(parsed, "--development-mode"),
      expectedBaselineFingerprint: required(parsed, "--expected-baseline"),
      confirmed: parsed.confirmed
    });
  }
  if (parsed.command === "claim-task") {
    return claimTask({
      ...common,
      id: required(parsed, "--item"),
      owner: required(parsed, "--owner"),
      operationId: required(parsed, "--operation")
    });
  }
  if (parsed.command === "retry-item") {
    return retryWorkItem({
      ...common,
      id: required(parsed, "--item"),
      expectedBaselineFingerprint: required(parsed, "--expected-baseline"),
      confirmed: parsed.confirmed
    });
  }
  const evidence = await readStructured(
    required(parsed, "--evidence"),
    "WORK_ITEM_EVIDENCE",
    { cwd: root, fs, stdin: io.stdin }
  );
  if (parsed.command === "task-result") {
    return recordTaskResult({
      ...common,
      id: required(parsed, "--item"),
      operationId: required(parsed, "--operation"),
      status: required(parsed, "--status"),
      evidence
    });
  }
  if (parsed.command === "delivery-item") {
    return recordDelivery({
      ...common,
      id: required(parsed, "--item"),
      action: required(parsed, "--action"),
      evidence
    });
  }
  return recordWorkItemGate({
    ...common,
    id: required(parsed, "--item"),
    status: required(parsed, "--status"),
    evidence
  });
}
async function runCli(argv, io = {}) {
  const stdout = io.stdout ?? ((value) => process.stdout.write(value));
  const stderr = io.stderr ?? ((value) => process.stderr.write(value));
  let jsonOutput = argv.includes("--json");
  try {
    const parsed = parse2(argv);
    jsonOutput = parsed.json;
    if (parsed.help || !parsed.command) {
      stdout(help);
      return 0;
    }
    if (!COMMANDS.includes(parsed.command)) throw new GatedLoopError("UNKNOWN_COMMAND", `Unknown command: ${parsed.command}`);
    if (parsed.command === "route" || parsed.command === "start") {
      const result3 = await runModeCommand(parsed, io);
      stdout(jsonOutput ? renderJson({ ok: true, result: result3 }) : renderJson(result3));
      return 0;
    }
    if (parsed.command === "prepare" || parsed.command === "freeze") {
      const result3 = await runBaselineCommand(parsed, io);
      stdout(jsonOutput ? renderJson({ ok: true, result: result3 }) : renderJson(result3));
      return 0;
    }
    if (parsed.command === "self-check" || parsed.command === "accept") {
      const result3 = await runGateCommand(parsed, io);
      stdout(jsonOutput ? renderJson({ ok: result3.status === "PASS", result: result3 }) : renderJson(result3));
      return result3.status === "PASS" ? 0 : 2;
    }
    if ([
      "prepare-item",
      "freeze-item",
      "revise-item",
      "ready-tasks",
      "task-context",
      "select-development-mode",
      "claim-task",
      "task-result",
      "retry-item",
      "gate-item",
      "delivery-item"
    ].includes(parsed.command)) {
      const result3 = await runWorkItemCommand(parsed, io);
      stdout(jsonOutput ? renderJson({ ok: true, result: result3 }) : renderJson(result3));
      return 0;
    }
    throw new GatedLoopError("UNKNOWN_COMMAND", `Unknown command: ${parsed.command}`);
  } catch (error) {
    const stable = error instanceof GatedLoopError ? error : new GatedLoopError("INTERNAL_ERROR", "Unexpected error");
    stderr(jsonOutput ? renderJson({ ok: false, error: { code: stable.code, message: stable.message, details: stable.details } }) : renderError(stable));
    return stable.exitCode;
  }
}
async function runHierarchicalCli(argv, io = {}) {
  const stdout = io.stdout ?? ((value) => process.stdout.write(value));
  const stderr = io.stderr ?? ((value) => process.stderr.write(value));
  const command = argv.find((value) => !value.startsWith("--"));
  if (!command || argv.includes("--help")) {
    stdout(hierarchicalHelp);
    return 0;
  }
  if (!HIERARCHICAL_COMMANDS.includes(command)) {
    const error = new GatedLoopError("UNKNOWN_COMMAND", `Unknown hdg command: ${command}`);
    const jsonOutput = argv.includes("--json");
    stderr(jsonOutput ? renderJson({ ok: false, error: { code: error.code, message: error.message, details: error.details } }) : renderError(error));
    return error.exitCode;
  }
  return runCli(argv, io);
}

// scripts/skill-cli-entry.mjs
process.exitCode = await runHierarchicalCli(process.argv.slice(2));
