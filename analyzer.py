"""
ブラックジャックのディーラー最終結果の確率分布と、
プレイヤーの各手の行動 (stand / hit / double / split) ごとの配当の期待値を計算し、
ベーシックストラテジーの表を求める。

前提:
  - 無限デッキ (各ランクの出現確率は一定)
  - 52枚が同確率 → A,2..9 は各 1/13、10/J/Q/K をまとめた 10 は 4/13
  - 手の状態は (合計値, ソフトかどうか) を持つ BaseHand で表す
    ソフト = エースを 11 として数えている状態
  - プレイヤーの手はナチュラル・ブラックジャックではないものとし、
    ディーラーのブラックジャックにはプレイヤーの 21 でも負ける
  - ディーラーがアップカード A / 10 でブラックジャックを確認するか (ピーク) は
    DEALER_PEEKS_ON_ACE / DEALER_PEEKS_ON_TEN で設定する。
    確認する場合はブラックジャックでないときだけ勝負するので、分布からブラックジャックを除く。
    確認しない場合はブラックジャックが分布に残り、ダブルダウンやスプリットの賭け金も全額負ける
"""
from __future__ import annotations
import abc
import io
import typing
import dataclasses
import collections.abc as cabc


UPCARDS: typing.Final[tuple[int, ...]] = (2, 3, 4, 5, 6, 7, 8, 9, 10, 1)
"""表を表示するときのディーラーのアップカードの並び (1 は A)"""


# **********************************************************
#  ブラックジャックのルール設定
# **********************************************************
HIT_SOFT_17: typing.Final[bool] = False
"""ディーラーの手が 17S の場合に hit するかどうかのフラグ"""

DOUBLE_AFTER_SPLITTING: typing.Final[bool] = False
"""Split 後に Double Down を許可するかどうかのフラグ"""

SPLIT_ACES_ONE_CARD: typing.Final[bool] = True
"""A を Split した場合、各手に配られる枚数を 1 枚だけに制限するかどうかのフラグ"""

MAX_SPLIT_HANDS: typing.Final[int] = 4
"""Split (再 Split を含む) で分けられる手の最大数。2 にすると再 Split なし"""

RESPLIT_ACES: typing.Final[bool] = False
"""A を Split した後、再び A が来た場合に再 Split を許可するかどうかのフラグ"""

BLACKJACK_PAYOUT: typing.Final[float] = 1.5
"""プレイヤーが Black Jack のときの配当倍率"""

DEALER_PEEKS_ON_ACE: typing.Final[bool] = True
"""アップカードが A のとき、プレイヤーの行動前にディーラーが Black Jack を確認するかどうかのフラグ"""

DEALER_PEEKS_ON_TEN: typing.Final[bool] = True
"""アップカードが 10 のとき、プレイヤーの行動前にディーラーが Black Jack を確認するかどうかのフラグ"""

DECK_COUNT_HI: typing.Final[float] = 0.0
"""デッキ 52 枚あたりのハイカード (A, 10, J, Q, K) の枚数の、標準 (20 枚) からの増減。
増減した分は 7〜9 の枚数で調整し、合計は 52 枚に保つ。0.0 なら標準のデッキ"""

DECK_COUNT_LO: typing.Final[float] = 0.0
"""デッキ 52 枚あたりのローカード (2〜6) の枚数の、標準 (20 枚) からの増減。
増減した分は 7〜9 の枚数で調整し、合計は 52 枚に保つ。0.0 なら標準のデッキ"""


def show_rule() -> None:
    print(f"  Dealer hits soft 17:         {HIT_SOFT_17!s}")
    print(f"  Dealer peeks on ace:         {DEALER_PEEKS_ON_ACE!s}")
    print(f"  Dealer peeks on ten:         {DEALER_PEEKS_ON_TEN!s}")
    print(f"  Double after splitting:      {DOUBLE_AFTER_SPLITTING!s}")
    print(f"  Split aces receive one card: {SPLIT_ACES_ONE_CARD!s}")
    print(f"  Max hands after splitting:   {MAX_SPLIT_HANDS:d}")
    print(f"  Resplit aces:                {RESPLIT_ACES!s}")
    print(f"  Blackjack Payout Ratio:   {BLACKJACK_PAYOUT:8.4f}")
    return


# **********************************************************
#  class DistCard
#    ... デッキからのカードの出現確率を表すクラス
# **********************************************************
class DistCard(cabc.Mapping[int, float]):
    """カードを引いた時の出現確率を保持する。"""
    __slots__ = ("__ratios",)

    def __init__(self, ratios: list[float]):
        assert len(ratios) == 10
        self.__ratios = ratios
        return

    def __len__(self) -> int:
        return 10
    
    def __getitem__(self, card: int) -> float:
        assert 1 <= card <= 10
        return self.__ratios[card - 1]

    def __iter__(self) -> cabc.Iterator[int]:
        yield from range(1, 11)

    @classmethod
    def get_standard(cls) -> typing.Self:
        return cls.get_counting_hilo(0.0, 0.0)

    @classmethod
    def get_counting_hilo(cls, count_hi: float, count_lo: float) -> typing.Self:
        """
        Hi-Lo の区分でデッキの構成を変えたカードの出現確率を返す。
          count_hi: 52 枚あたりのハイカード (A, 10, J, Q, K) の枚数の、標準 (20 枚) からの増減
          count_lo: 52 枚あたりのローカード (2〜6) の枚数の、標準 (20 枚) からの増減
        増減した分は 7〜9 (標準 12 枚) で調整し、合計は 52 枚に保つ。
        """
        ratio_hi = (4.0 + count_hi / 5.0) / 52.0
        ratio_lo = (4.0 + count_lo / 5.0) / 52.0
        ratio_md = (4.0 - (count_hi + count_lo) / 3.0) / 52.0

        ratio: list[float] = [
            ratio_hi, ratio_lo, ratio_lo, ratio_lo, ratio_lo,
            ratio_lo, ratio_md, ratio_md, ratio_md, ratio_hi * 4.0, ]
        return cls(ratio)

    def show(self) -> None:
        for i in range(2, 10):
            print("  {:2d}: 1/13 x {:12.8f}".format(i, self[i] * 13.0))
        print("  10: 4/13 x {:12.8f}".format(self[10] * 13.0 / 4.0))
        print("   A: 1/13 x {:12.8f}".format(self[1] * 13.0))
        return


# **********************************************************
#  class DistScore
#    ... 最終的に得られる点数の確率分布を表すクラス
# **********************************************************
class DistScore(dict[int, float]):
    __slots__ = ()

    def merge(self, other: DistScore, coeff: float = 1.0) -> None:
        """ 確率分布の足し算 [self] += [others] * coeff を行う"""
        for key, value in other.items():
            self[key] = self.get(key, 0.0) + value * coeff
        return

    def normalize(self) -> None:
        """確率分布の合計が 1.0 になるよう正規化する"""
        total: float = sum(self.values())
        for key in self:
            self[key] /= total
        return

    def __getitem__(self, score: int) -> float:
        """"点数scoreに対する確率を返す"""
        return self.get(score, 0.0)


def calc_ev(player: DistScore, dealer: DistScore) -> float:
    """お金を1.0掛けた場合のplayerへの配当の期待値を計算する。"""

    # お金を1.0掛けた場合のplayerへの配当期待値の結果を格納する
    ret: float = 0.0

    for dealer_score, dealer_prob in dealer.items():
        for player_score, player_prob in player.items():
            # ディーラーとプレイヤーの点数が (dealer_score, player_score) の
            # 組合せの場合を考える。このときの確率が下記 prob。
            prob: float = dealer_prob * player_prob

            # 場合に応じて配当を決める
            payoff: float
            if player_score == HAND_BJ.score:
                if dealer_score == HAND_BJ.score:
                    # dealer/player 双方がブラックジャックの場合、引き分け
                    payoff = 0.0
                else:
                    # player だけブラックジャックの場合、配当を上乗せしてもらえる
                    payoff = BLACKJACK_PAYOUT
            elif player_score == HAND_BUST.score:
                # プレイヤーがバーストしている場合、ディーラーのスコアに関係なく負け
                payoff = -1.0
            elif dealer_score > player_score:
                # スコア比較で dealer の勝ち
                payoff = -1.0
            elif dealer_score < player_score:
                # スコアで player の勝ち
                payoff = 1.0
            else:
                # 上記以外は引き分けで配当なし
                payoff = 0.0
            ret += payoff * prob
    return ret


# **********************************************************
#  ブラックジャックの手を表すクラス
# **********************************************************
#   BaseHand
#    ├ HandBust
#    ├ HandBlackjack
#    └ Hand
#       ├ HandMulti
#       └ HandSingle
@dataclasses.dataclass(frozen=True)
class BaseHand(abc.ABC):
    """手の状態。イミュータブル (frozen) で、辞書のキーに使える。
    frozen なので、サブクラスも frozen な dataclass にする必要がある。"""

    @property
    @abc.abstractmethod
    def score(self) -> int:
        """手の合計値を返す。BUSTは-100、natural blackjackは+100。"""
        ...

    @abc.abstractmethod
    def __str__(self) -> str:
        ...


@dataclasses.dataclass(frozen=True)
class HandBust(BaseHand):
    """バースト済みの手。"""

    @property
    def score(self) -> int:
        return -100

    def __str__(self) -> str:
        return "BUS"


@dataclasses.dataclass(frozen=True)
class HandBlackjack(BaseHand):
    """ナチュラル・ブラックジャックの手。"""

    @property
    def score(self) -> int:
        return +100

    def __str__(self) -> str:
        return "BJ"


HAND_BUST: typing.Final[HandBust] = HandBust()
HAND_BJ: typing.Final[HandBlackjack] = HandBlackjack()


@dataclasses.dataclass(frozen=True)
class Hand(BaseHand, abc.ABC):
    """一般的な手の状態 (ディーラー・プレイヤー共通)。"""

    @property
    @abc.abstractmethod
    def soft(self) -> bool:
        """ソフトハンドであるかどうかを返す。"""
        ...

    @abc.abstractmethod
    def add(self, card: int) -> BaseHand:
        """カードを1枚加えた新しい状態を返す。エースの扱いはここで処理する。"""
        ...

    @classmethod
    def get_label(cls, card: int) -> str:
        if card == 1:
            return "A"
        elif card == 10:
            return "T"
        return str(card)


@dataclasses.dataclass(frozen=True)
class HandMulti(Hand):
    """複数枚から構成される手の状態。"""
    _score: int = 0
    _soft: bool = False

    @property
    def score(self) -> int:
        return self._score

    @property
    def soft(self) -> bool:
        return self._soft

    def add(self, card: int) -> HandMulti | HandBust:
        """カードを1枚加えた新しい状態を返す。エースの扱いはここで処理する。"""
        score: int
        soft: bool
        score, soft = self._score, self._soft

        if card == 1 and not soft and score <= 10:
            score += 11
            soft = True
        else:
            score += card

        # 21 を超えたら、11 として数えているエースを 1 に戻す
        if score > 21:
            if soft:
                score -= 10
                soft = False
            else:
                return HAND_BUST
        return HandMulti(score, soft)

    def __str__(self) -> str:
        """表に表示するための幅 4 文字のラベル。ソフトは "A, 7" (A と残りの合計)、ハードは " 18 "。"""
        if self._soft:
            return f"A,{self._score - 11:>2d}"
        return f"{self._score:>3d} "


@dataclasses.dataclass(frozen=True)
class HandSingle(Hand):
    card: int

    def add(self, card: int) -> HandMulti | HandBlackjack:
        """カードを1枚加えた新しい状態を返す。"""
        soft = 1 in (self.card, card)
        score = self.card + card + (10 if soft else 0)
        if score == 21:
            return HAND_BJ
        return HandMulti(score, soft)

    def add_no_bj(self, card: int) -> HandMulti:
        hand = self.add(card)
        if isinstance(hand, HandBlackjack):
            return HandMulti(21, True)
        return hand

    @property
    def score(self) -> int:
        return 11 if self.card == 1 else self.card

    @property
    def soft(self) -> bool:
        return self.card == 1

    def __str__(self) -> str:
        return "({:s})".format(self.get_label(self.card))

    @classmethod
    def get_label(cls, card: int) -> str:
        return "A" if card == 1 else str(card)


# **********************************************************
#  ディーラーの手の結果分布を表すクラス
# **********************************************************
class DealerDistribution:
    __slots__ = ("__card_probs", "__cache_score")

    def __init__(self, card_probs: DistCard):
        self.__card_probs: typing.Final[DistCard] = card_probs

        self.__cache_score: dict[Hand, DistScore] = {}
        for score in range(17, 22):
            hand = HandMulti(score, False)
            self.__cache_score[hand] = DistScore({hand.score: 1.0})
        for score in range(18 if HIT_SOFT_17 else 17, 22):
            hand = HandMulti(score, True)
            self.__cache_score[hand] = DistScore({hand.score: 1.0})
        return

    def __getitem__(self, upcard: int) -> DistScore:
        hand = HandSingle(upcard)
        score_dist = self.__calc_dist(hand)
        return score_dist

    def __calc_dist(self, hand: Hand) -> DistScore:
        if hand in self.__cache_score:
            return self.__cache_score[hand]

        score_dist: DistScore = DistScore()
        for card, prob in self.__card_probs.items():
            new_hand: BaseHand = hand.add(card)
            match new_hand:
                case HandBust() | HandBlackjack():
                    score = new_hand.score
                    score_dist[score] = score_dist.get(score, 0.0) + prob
                case HandMulti():
                    new_score_dist: DistScore = self.__calc_dist(new_hand)
                    score_dist.merge(new_score_dist, prob)
                case _:
                    raise NotImplementedError()

        # ディーラーがブラックジャックを確認する (ピーク) 場合は、
        # ブラックジャックでないと分かった後の分布に変換する (確認しない場合は残す)
        if isinstance(hand, HandSingle):
            if ((hand.card == 1 and DEALER_PEEKS_ON_ACE) or
                    (hand.card == 10 and DEALER_PEEKS_ON_TEN)):
                # ブラックジャックの確率が 0 でキーがない場合もあるので pop で取り除く
                score_dist.pop(HAND_BJ.score, None)
                score_dist.normalize()

        # 結果をキャッシュに保存し、次に同じ状態に来たときは再利用する。
        # HandSingle (アップカード) の結果は、ピークで正規化した後の分布を保存する。
        # ピークのルール (DEALER_PEEKS_ON_ACE / DEALER_PEEKS_ON_TEN) はプログラムを通して変わらない前提。
        # HandSingle と HandMulti は別のクラスなので、キャッシュのキーが重なることはない。
        self.__cache_score[hand] = score_dist

        return score_dist

    def show(self) -> None:

        # ヘッダー行の表示
        sio = io.StringIO()
        sio.write("   {:>2s}".format("up"))
        for score in range(17, 22):
            sio.write(" |{:>9d}".format(score))
        sio.write(" |{:>9s}".format("BL JACK"))
        sio.write(" |{:>9s}".format("BUST"))
        header: str = sio.getvalue()
        print(header)

        # 横罫線の生成と表示
        line_string: str = "  " + "-" * (len(header) - 1)
        print(line_string)

        # 表の表示
        for upcard in UPCARDS:
            score_dist = self[upcard]
            sio = io.StringIO()
            sio.write("   {:>2s}".format(HandSingle.get_label(upcard)))
            # 確率はパーセント表記で表示する (書式 "%" は値を 100 倍して % を付ける)
            for score in range(17, 22):
                sio.write(" |{:>9.3%}".format(score_dist[score]))
            sio.write(" |{:>9.3%}".format(score_dist[HAND_BJ.score]))
            sio.write(" |{:>9.3%}".format(score_dist[HAND_BUST.score]))
            assert round(sum(score_dist.values()), 8) == 1.0
            print(sio.getvalue())

        # 一番下の横罫線の表示
        print(line_string)
        return


ACTION_NAMES: typing.Final[dict[str, str]] = {"S": "stand", "H": "hit", "DD": "double", "SP": "split"}
"""行動の記号と、表に表示する名前の対応"""


class PlayerHandTable:
    """ディーラー分布を固定し、プレイヤーの各手の stand / hit-1 / hit-more の期待値を計算する。"""
    __slots__ = ("__dealer_dist", "__cache_stand", "__cache_hitm", "__cache_hit1", "__card_probs")

    def __init__(self, dealer_dist: DistScore, card_probs: cabc.Mapping[int, float]) -> None:
        self.__dealer_dist: typing.Final[DistScore] = dealer_dist
        self.__cache_stand: dict[BaseHand, float] = {}
        self.__cache_hit1: dict[Hand, float] = {}
        self.__cache_hitm: dict[Hand, float] = {}
        self.__card_probs: cabc.Mapping[int, float] = card_probs
        return

    def stand(self, hand: BaseHand) -> float:
        """今の手でスタンドした場合の期待値。"""
        if hand in self.__cache_stand:
            return self.__cache_stand[hand]

        dist = DistScore({hand.score: 1.0})
        ev = calc_ev(dist, self.__dealer_dist)
        self.__cache_stand[hand] = ev
        return ev

    def hit1(self, hand: Hand) -> float:
        """1枚だけ引いてスタンドした場合の期待値。"""
        if hand in self.__cache_hit1:
            return self.__cache_hit1[hand]

        ev = 0.0
        for card, prob in self.__card_probs.items():
            new_hand: BaseHand = hand.add(card)
            ev += self.stand(new_hand) * prob
        self.__cache_hit1[hand] = ev
        return ev

    def best(self, hand: BaseHand) -> float:
        """今の手から最適に行動した場合の期待値"""
        ev = self.stand(hand)
        if isinstance(hand, Hand):
            ev = max(ev, self.hit_more(hand))
        return ev

    def hit_more(self, hand: Hand) -> float:
        """1枚引き、その後は期待値が下がらない限り引き続けた場合"""
        if hand in self.__cache_hitm:
            return self.__cache_hitm[hand]

        ev: float = 0.0
        for card, prob in self.__card_probs.items():
            new_hand = hand.add(card)
            ev += self.best(new_hand) * prob
        self.__cache_hitm[hand] = ev
        return ev

    def double(self, hand: HandMulti) -> float:
        """ダブルダウンした場合の期待値 (賭け金 2 倍で 1 枚だけ引く)。
        ディーラーのブラックジャックには 2 倍分負けるので、単純に hit1 の 2 倍になる。"""
        return 2.0 * self.hit1(hand)

    def after_split(self, card: int, card2: int) -> float:
        """card でスプリットした手が 2 枚目 card2 を受け取った後、その手 1 つの期待値 (再スプリットしない場合)。
        A+10 は普通の 21 として扱う (ブラックジャックにはならない)。"""
        hand: HandMulti = HandSingle(card).add_no_bj(card2)
        if card == 1 and SPLIT_ACES_ONE_CARD:
            # A のスプリットは 1 枚だけ配られてスタンド
            return self.stand(hand)
        # 最適に行動する (DAS ならダブルダウンも選べる)
        ev: float = self.best(hand)
        if DOUBLE_AFTER_SPLITTING:
            ev = max(ev, self.double(hand))
        return ev

    def split(self, card: int) -> float:
        """
        card のペアをスプリットした場合の期待値 (分かれた全ての手の合計)。
        2 枚目に同じ card が来た場合は、手の数が MAX_SPLIT_HANDS 未満なら再スプリットできる
        (A は RESPLIT_ACES のときのみ)。再スプリットするかどうかは期待値の高い方を選ぶ。
        """
        can_resplit: bool = (card != 1) or RESPLIT_ACES
        p_pair: float = self.__card_probs[card]

        # 2 枚目がペア以外だった場合の期待値の合計 (確率で重み付け済み)
        ev_other: float = sum(
            prob * self.after_split(card, card2)
            for card2, prob in self.__card_probs.items() if card2 != card)
        # 2 枚目が同じカードで、再スプリットせずにそのまま行動する場合の期待値
        ev_pair: float = self.after_split(card, card)

        cache: dict[tuple[int, int], float] = {}

        def remaining(waiting: int, hands: int) -> float:
            """2 枚目を待っている手が waiting 個、手の総数が hands のとき、待っている手全体の期待値。"""
            if waiting == 0:
                return 0.0
            key = (waiting, hands)
            if key in cache:
                return cache[key]

            # 待っている手の 1 つに 2 枚目を配る
            ev_keep: float = ev_pair + remaining(waiting - 1, hands)  # ペアのまま行動
            ev: float = ev_other + (1.0 - p_pair) * remaining(waiting - 1, hands)
            if can_resplit and hands < MAX_SPLIT_HANDS:
                # 再スプリットすると、待っている手が 1 つ増え、手の総数も 1 つ増える
                ev += p_pair * max(ev_keep, remaining(waiting + 1, hands + 1))
            else:
                ev += p_pair * ev_keep
            cache[key] = ev
            return ev

        # 最初のスプリットで 2 手に分かれ、どちらも 2 枚目を待っている
        return remaining(2, 2)

    def best_action(self, hand: HandMulti, can_double: bool = True,
                    pair_card: int | None = None) -> tuple[float, str]:
        """
        選べる行動の中で期待値が最大のものを (期待値, 行動の記号) で返す。
          can_double: ダブルダウンを候補に含めるか (最初の 2 枚なら True、3 枚目以降は False)
          pair_card: ペアの場合はそのカード。指定するとスプリットを候補に含める
        行動の記号は "S" / "H" / "DD" / "SP"。期待値が等しい場合は S, H, DD, SP の順に優先する。
        """
        candidates: list[tuple[float, str]] = [(self.stand(hand), "S"), (self.hit_more(hand), "H")]
        if can_double:
            candidates.append((self.double(hand), "DD"))
        if pair_card is not None:
            candidates.append((self.split(pair_card), "SP"))
        # max は期待値が等しい場合に先に現れた候補を返すので、上の順に優先される
        return max(candidates, key=lambda x: x[0])

    def show_base(self) -> None:
        # action: 3 枚目以降 (ダブルダウン不可) の行動、first: 最初の 2 枚 (ダブルダウン可) の行動

        # ヘッダ行の表示
        sio = io.StringIO()
        sio.write("{:>8s}".format("hand"))
        sio.write(" |{:>11s}".format("stand"))
        sio.write(" |{:>11s}".format("hit-1"))
        sio.write(" |{:>11s}".format("hit-more"))
        sio.write(" |{:>11s}".format("double"))
        sio.write(" |{:>7s}".format("action"))
        sio.write(" |{:>7s}".format("first"))
        header: str = sio.getvalue()
        print(header)

        sio = io.StringIO()
        sio.write("{:>8s}".format(""))
        sio.write(" |{:>11s}".format("ev"))
        sio.write(" |{:>11s}".format("ev"))
        sio.write(" |{:>11s}".format("ev"))
        sio.write(" |{:>11s}".format("ev"))
        sio.write(" |{:>7s}".format(""))
        sio.write(" |{:>7s}".format("action"))
        header: str = sio.getvalue()
        print(header)

        # 横罫線の生成
        line_string: str = "  " + "-" * (len(header) - 1)

        # 行見出しとなるプレイヤーの初手を生成
        # None にした場所に横罫線が挿入されるようになっている
        hands: list[HandMulti | None] = \
            [None, ] + \
            [HandMulti(t, False) for t in range(2, 22)] + \
            [None, ] + \
            [HandMulti(t, True) for t in range(11, 22)] + \
            [None, ]

        # 表の生成
        for hand in hands:

            # hand = None の行は横罫線を引くだけ
            if hand is None:
                print(line_string)
                continue

            # act_base  ... 2手目以降の
            # act_first ... 初手の最適戦略。Double Down を含めてアクションを選択
            _, act_base = self.best_action(hand, can_double=False)
            _, act_first = self.best_action(hand)

            sio = io.StringIO()
            sio.write("{:>8s}".format(str(hand)))
            sio.write(" |{:>11.6f}".format(self.stand(hand)))
            sio.write(" |{:>11.6f}".format(self.hit1(hand)))
            sio.write(" |{:>11.6f}".format(self.hit_more(hand)))
            sio.write(" |{:>11.6f}".format(self.double(hand)))
            sio.write(" |{:>7s}".format(ACTION_NAMES[act_base]))
            sio.write(" |{:>7s}".format(ACTION_NAMES[act_first]))
            print(sio.getvalue())

        return

    def show_split(self) -> None:

        sio = io.StringIO()
        sio.write("{:>8s}".format("pair"))
        sio.write(" | {:>9s}".format("no-split ev"))
        sio.write(" | {:>9s}".format("split ev"))
        sio.write(" | {:>6s}".format("action"))
        header: str = sio.getvalue()
        line_string: str = "  " + "-" * (len(header) - 1)
        print(header)
        print(line_string)

        for card in UPCARDS:
            # ペア (A,A も含む) はブラックジャックにならないので add_no_bj で HandMulti を得る
            hand: HandMulti = HandSingle(card).add_no_bj(card)
            ev_no_split, _ = self.best_action(hand)                 # スプリットしない場合
            _, act_split = self.best_action(hand, pair_card=card)   # スプリットも含めた場合

            sio = io.StringIO()
            sio.write("{:>8s}".format("{0:s}-{0:s}".format(HandSingle.get_label(card))))
            sio.write(" | {:>9.5f}".format(ev_no_split))
            sio.write(" | {:>9.5f}".format(self.split(card)))
            sio.write(" | {:>6s}".format(ACTION_NAMES[act_split]))
            print(sio.getvalue())

        print(line_string)
        return

    @staticmethod
    def show_strategy(tables: dict[int, PlayerHandTable]) -> None:
        sio = io.StringIO()
        sio.write("    {:>5s}".format("HAND"))
        for card in tables:
            sio.write(" | {:>2}".format(HandSingle.get_label(card)))
        header: str = sio.getvalue()
        line_string: str = "  " + "-" * (len(header) - 1)
        print(header)

        hands: list[HandMulti | int | None] = \
            [None, ] + \
            [HandMulti(t, False) for t in range(4, 21)] + \
            [None, ] + \
            [HandMulti(t, True) for t in range(13, 21)] + \
            [None, ] + \
            [card for card in tables] + \
            [None, ]

        for _hand in hands:
            if _hand is None:
                print(line_string)
                continue

            sio = io.StringIO()

            hand: HandMulti
            pair_card: int | None
            if isinstance(_hand, HandMulti):
                hand, pair_card = _hand, None
                sio.write("{:>9s}".format(str(hand)))
            else:
                hand, pair_card = HandSingle(_hand).add_no_bj(_hand), _hand
                sio.write("    {0:>2s}-{0:>2s}".format(HandSingle.get_label(_hand)))

            for table in tables.values():
                _, act = table.best_action(hand, pair_card=pair_card)
                sio.write(" | {:>2}".format(act))
            print(sio.getvalue())

        return


def main():
    print()

    # ------------------------------
    #  設定されたルールの表示
    # ------------------------------
    print("Rules to be adopted in this simulation:")
    show_rule()
    print()

    # ------------------------------
    #  CARD_PROBS: デッキにあるカードの割合
    # ------------------------------
    CARD_PROBS: typing.Final[DistCard] = DistCard.get_counting_hilo(DECK_COUNT_HI, DECK_COUNT_LO)

    print("Card probabilities:")
    CARD_PROBS.show()
    print()

    # ------------------------------
    # dealer_dists: dealer の初手カードに対する結果の分布
    # ------------------------------
    dealer_dists = DealerDistribution(CARD_PROBS)

    print("Dealer final distribution:")
    dealer_dists.show()
    print()

    # upcardを固定し、playerの戦略を計算
    upcard: int = 6
    dealer_score_dist: DistScore = dealer_dists[upcard]

    table = PlayerHandTable(dealer_score_dist, CARD_PROBS)

    # 結果表示
    print("Player hands vs dealer {}".format(HandSingle.get_label(upcard)))
    table.show_base()
    print()

    # ペアの戦略: スプリットしない場合 (最初の 2 枚として最適な行動) とスプリットした場合を比べる
    print("Pairs vs dealer {}".format(HandSingle.get_label(upcard)))
    table.show_split()
    print()

    tables: dict[int, PlayerHandTable] = {
        card: PlayerHandTable(dealer_dists[card], CARD_PROBS)
        for card in UPCARDS}

    print("Overall strategy:")
    PlayerHandTable.show_strategy(tables)
    print()


if __name__ == "__main__":
    main()
