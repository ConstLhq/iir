#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Hierarchical Dirichlet Process - Latent Dirichlet Allocation
# (c)2010-2011 Nakatani Shuyo / Cybozu Labs Inc.
# (refer to "Hierarchical Dirichlet Processes"(Teh et.al, 2005))

import optparse
import numpy
import vocabulary

class HDPLDA:
    def __init__(self, alpha, gamma, base):
        self.alpha = alpha
        self.base = base
        self.gamma = gamma

        # cache of calculation
        self.cur_log_base_cache = [0]
        self.cur_log_V_base_cache = [0]

    def set_corpus(self, corpus, stopwords, K):
        self.x_ji = [] # vocabulary for each document and term
        self.t_ji = [] # table for each document and term
        self.k_jt = [] # topic for each document and table
        self.n_jt = [] # number of terms for each document and table

        self.tables = [] # available id of tables for each document
        self.n_terms = 0
        self.n_tables = 0

        self.m_k = numpy.zeros(K, dtype=int) # number of tables for each topic
        self.n_k = [0] * K  # number of terms for each topic

        voca = vocabulary.Vocabulary(stopwords==0)

        docs = [voca.doc_to_ids(doc) for doc in corpus]
        docs = voca.cut_low_freq(docs)
        for x_i in docs:
            N = len(x_i)
            self.x_ji.append(x_i)
            self.n_terms += N

            self.k_jt.append(range(K))
            t_i = numpy.random.randint(0, K, N)
            self.t_ji.append(t_i)

            n_t = numpy.zeros(K, dtype=int)
            for t in t_i: n_t[t] += 1
            self.n_jt.append(n_t)
            for t, n in enumerate(n_t): self.n_k[t] += n

            tables = [t for t, n in enumerate(n_t) if n > 0]
            self.tables.append(tables)
            self.n_tables += len(tables)
            for t in tables: self.m_k[t] += 1

        self.topics = [k for k, m in enumerate(self.m_k) if m > 0] # available id of topics

        self.V = voca.size()
        self.n_kv = numpy.zeros((K, self.V), dtype=int) # number of terms for each topic and vocabulary
        for j, x_i in enumerate(self.x_ji):
            for i, v in enumerate(x_i):
                self.n_kv[self.t_ji[j][i], v] += 1

        self.updated_n_tables()

        self.gamma_f_k_new_x_ji = self.gamma / self.V
        self.Vbase = self.V * self.base

        return voca

    def dump(self, disp_x=False):
        if disp_x: print "x_ji:", self.x_ji
        print "t_ji:", self.t_ji
        print "k_jt:", self.k_jt
        print "n_kv:", self.n_kv
        print "n_jt:", self.n_jt
        print "n_k:", self.n_k
        print "m_k:", self.m_k
        print "tables:", self.tables
        print "topics:", self.topics

    # cache for faster calcuration
    def updated_n_tables(self):
        self.alpha_over_T_gamma = self.alpha / (self.n_tables + self.gamma)

    def cur_log_base(self, n):
        """cache of \sum_{i=0}^{n-1} numpy.log(i + self.base)"""
        N = len(self.cur_log_base_cache)
        if n < N: return self.cur_log_base_cache[n]
        s = self.cur_log_base_cache[-1]
        while N <= n:
            s += numpy.log(N + self.base - 1)
            self.cur_log_base_cache.append(s)
            N += 1
        return s

    def cur_log_V_base(self, n):
        """cache of \sum_{i=0}^{n-1} numpy.log(i + self.base * self.V)"""
        N = len(self.cur_log_V_base_cache)
        if n < N: return self.cur_log_V_base_cache[n]
        s = self.cur_log_V_base_cache[-1]
        while N <= n:
            s += numpy.log(N + self.Vbase - 1)
            self.cur_log_V_base_cache.append(s)
            N += 1
        return s

    # n_??/m_? を用いて f_k を高速に計算
    def f_k_x_ji_fast(self, k, v):
        return (self.n_kv[k, v] + self.base) / (self.n_k[k] + self.Vbase)

    def log_f_k_new_x_jt_fast2(self, n_jt, n_tv, n_kv = None, n_k = 0):
        p = self.cur_log_V_base(n_k) - self.cur_log_V_base(n_k + n_jt)
        for (v_l, n_l) in n_tv:
            n0 = n_kv[v_l] if n_kv != None else 0
            p += self.cur_log_base(n0 + n_l) - self.cur_log_base(n0)
        return p

    def count_n_jtv(self, j, t):
        x_i = self.x_ji[j]
        t_i = self.t_ji[j]
        n_jtv = dict()
        n_jt = 0
        for i, t1 in enumerate(t_i):
            if t1 == t:
                v = x_i[i]
                if v in n_jtv:
                    n_jtv[v] += 1
                else:
                    n_jtv[v] = 1
                n_jt += 1
        return (n_jt, n_jtv.items())

    # sampling topic
    # 新しいトピックの場合、パラメータの領域を確保
    def sampling_topic(self, p_k):
        drawing = numpy.random.multinomial(1, p_k / p_k.sum()).argmax()
        # 新しいトピック
        if drawing < len(self.topics):
            k_new = self.topics[drawing]
        else:
            # 空きトピックIDを取得(あれば再利用)
            K = self.m_k.size
            for k_new in range(K):
                if k_new not in self.topics: break
            else:
                # なければ新しいテーブルID
                k_new = K
                self.n_k.append(0)
                self.m_k = numpy.resize(self.m_k, K + 1)
                self.m_k[k_new] = 0
                self.n_kv = numpy.resize(self.n_kv, (k_new+1, self.V))
                self.n_kv[k_new, :] = numpy.zeros(self.V, dtype=int)
            self.topics.append(k_new)
        return k_new

    # 客 x_ji を新しいテーブルに案内
    # テーブルのトピック(料理)もサンプリング
    def new_table(self, j, i, f_k):
        # 空きテーブルIDを取得
        T_j = self.n_jt[j].size
        for t_new in range(T_j):
            if t_new not in self.tables[j]: break
        else:
            # 新しいテーブルID
            t_new = T_j
            self.n_jt[j].resize(t_new+1) # self.n_jt[j].append(0)
            self.k_jt[j].append(0)
        self.tables[j].append(t_new)
        self.n_tables += 1
        self.updated_n_tables()

        # sampling of k (新しいテーブルの料理(トピック))
        p_k = [self.m_k[k] * f_k[k] for k in self.topics]
        p_k.append(self.gamma_f_k_new_x_ji) # self.gamma * self.f_k_new_x_ji_fast()
        k_new = self.sampling_topic(numpy.array(p_k, copy=False))

        self.k_jt[j][t_new] = k_new
        self.m_k[k_new] += 1

        return t_new


    # sampling t (table) from posterior
    def sampling_t(self, j, i):
        v = self.x_ji[j][i]
        tables = self.tables[j]
        t_old = self.t_ji[j][i]
        k_old = self.k_jt[j][t_old]

        self.n_kv[k_old, v] -= 1
        self.n_k[k_old] -= 1
        self.n_jt[j][t_old] -= 1

        if self.n_jt[j][t_old]==0:
            # 客がいなくなったテーブル
            tables.remove(t_old)
            self.m_k[k_old] -= 1
            self.n_tables -= 1
            self.updated_n_tables()

            if self.m_k[k_old] == 0:
                # 客がいなくなった料理(トピック)
                self.topics.remove(k_old)

        # sampling of t ( p(t_ji=t) を求める )
        f_k = numpy.zeros(self.m_k.size)
        for k in self.topics:
            f_k[k] = self.f_k_x_ji_fast(k, v)
        p_t = [self.n_jt[j][t] * f_k[self.k_jt[j][t]] for t in tables]
        p_x_ji = numpy.inner(self.m_k, f_k) + self.gamma_f_k_new_x_ji
        p_t.append(p_x_ji * self.alpha_over_T_gamma)

        p_t = numpy.array(p_t, copy=False)
        p_t /= p_t.sum()
        drawing = numpy.random.multinomial(1, p_t).argmax()
        if drawing < len(tables):
            t_new = tables[drawing]
        else:
            t_new = self.new_table(j, i, f_k)

        # update counters
        self.t_ji[j][i] = t_new
        self.n_jt[j][t_new] += 1

        k_new = self.k_jt[j][t_new]
        self.n_k[k_new] += 1
        self.n_kv[k_new, v] += 1

    # sampling k (dish=topic) from posterior
    def sampling_k(self, j, t):
        k_old = self.k_jt[j][t]
        self.m_k[k_old] -= 1
        self.n_k[k_old] -= self.n_jt[j][t]
        if self.m_k[k_old] > 0:
            for v, t1 in zip(self.x_ji[j], self.t_ji[j]):
                if t1 != t: continue
                self.n_kv[k_old, v] -= 1
        else:
            self.topics.remove(k_old)

        # sampling of k
        # 確率が小さくなりすぎるので log で保持。最大値を引いてからexp&正規化
        n_jt, n_jtv = self.count_n_jtv(j, t)
        K = len(self.topics)
        log_p_k = numpy.zeros(K+1)
        for i, k in enumerate(self.topics):
            log_p_k[i] = self.log_f_k_new_x_jt_fast2(n_jt, n_jtv, self.n_kv[k, :], self.n_k[k]) + numpy.log(self.m_k[k])
        log_p_k[K] = self.log_f_k_new_x_jt_fast2(n_jt, n_jtv) + numpy.log(self.gamma)
        k_new = self.sampling_topic(numpy.exp(log_p_k - log_p_k.max()))

        # update counters
        self.k_jt[j][t] = k_new
        self.m_k[k_new] += 1
        self.n_k[k_new] += self.n_jt[j][t]
        for v, t1 in zip(self.x_ji[j], self.t_ji[j]):
            if t1 != t: continue
            self.n_kv[k_new, v] += 1

    def inference(self):
        for j, x_i in enumerate(self.x_ji):
            for i in range(len(x_i)):
                self.sampling_t(j, i)
            for t in self.tables[j]:
                self.sampling_k(j, t)

    def worddist(self):
        return [(self.n_kv[k] + self.base) / (self.n_k[k] + self.Vbase) for k in self.topics]

    def perplexity(self):
        phi = self.worddist()
        phi.append(numpy.zeros(self.V) + 1.0 / self.V)
        log_per = 0
        N = 0
        for j, x_i in enumerate(self.x_ji):
            p_k = numpy.zeros(self.m_k.size)    # topic dist for document 
            for t in self.tables[j]:
                k = self.k_jt[j][t]
                p_k[k] += self.n_jt[j][t]
            p_k /= len(x_i) + self.alpha
            
            p_k_parent = self.alpha / (len(x_i) + self.alpha)
            p_k += p_k_parent * (self.m_k / (self.n_tables + self.gamma))
            
            theta = [p_k[k] for k in self.topics]
            theta.append(p_k_parent * (self.gamma / (self.n_tables + self.gamma)))

            for v in x_i:
                log_per -= numpy.log(numpy.inner([p[v] for p in phi], theta))
            N += len(x_i)
        return numpy.exp(log_per / N)


def hdplda_learning(hdplda, iteration):
    for i in range(iteration):
        print "-%d K=%d p=%f" % (i + 1, len(hdplda.topics), hdplda.perplexity())
        hdplda.inference()
    print "K=%d perplexity=%f" % (len(hdplda.topics), hdplda.perplexity())
    return hdplda

def main():
    parser = optparse.OptionParser()
    parser.add_option("-f", dest="filename", help="corpus filename")
    parser.add_option("-c", dest="corpus", help="using range of Brown corpus' files(start:end)")
    parser.add_option("--alpha", dest="alpha", type="float", help="parameter alpha", default=numpy.random.gamma(1, 1))
    parser.add_option("--gamma", dest="gamma", type="float", help="parameter gamma", default=numpy.random.gamma(1, 1))
    parser.add_option("--base", dest="base", type="float", help="parameter of base measure H", default=0.5)
    parser.add_option("-k", dest="K", type="int", help="initial number of topics", default=1)
    parser.add_option("-i", dest="iteration", type="int", help="iteration count", default=10)
    parser.add_option("-s", dest="stopwords", type="int", help="0=exclude stop words, 1=include stop words", default=1)
    parser.add_option("--seed", dest="seed", type="int", help="random seed")
    (options, args) = parser.parse_args()
    if not (options.filename or options.corpus): parser.error("need corpus filename(-f) or corpus range(-c)")
    if options.seed != None:
        numpy.random.seed(options.seed)
        print "seed = ", options.seed

    if options.filename:
        corpus = vocabulary.load_file(options.filename)
    else:
        corpus = vocabulary.load_corpus(options.corpus)
        if not corpus: parser.error("corpus range(-c) forms 'start:end'")

    hdplda = HDPLDA( options.alpha, options.gamma, options.base )
    voca = hdplda.set_corpus(corpus, options.stopwords, options.K)
    print "corpus=%d words=%d alpha=%f gamma=%f base=%f initK=%d stopwords=%d" % (len(corpus), len(voca.vocas), options.alpha, options.gamma, options.base, options.K, options.stopwords)
    #hdplda.dump()

    import cProfile
    cProfile.runctx('hdplda_learning(hdplda, options.iteration)', globals(), locals(), 'hdplda.profile')
    #hdplda_learning(hdplda, options.iteration)

    phi = hdplda.worddist()
    for k, phi_k in enumerate(phi):
        print "\n-- topic: %d" % k
        for w in numpy.argsort(-phi_k)[:20]:
            print "%s: %f" % (voca[w], phi_k[w])

if __name__ == "__main__":
    main()
